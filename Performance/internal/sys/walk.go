package sys

import (
	"container/heap"
	"context"
	"io/fs"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"sync"
	"sync/atomic"
	"time"
)

// FileVisit is called for every regular file found by Walk. It must be safe
// for concurrent use.
type FileVisit func(path string, size int64, mod time.Time)

// WalkStats summarises a walk.
type WalkStats struct {
	Bytes   int64
	Files   int64
	Dirs    int64
	Skipped int64 // unreadable directories
}

// WalkOptions tunes Walk.
type WalkOptions struct {
	SkipDir func(path, name string) bool // return true to not descend
	OnFile  FileVisit
	Workers int
}

// Walk traverses root in parallel without following symlinks or junctions,
// counting cloud placeholders as zero bytes.
func Walk(ctx context.Context, root string, opt WalkOptions) WalkStats {
	var st WalkStats
	workers := opt.Workers
	if workers <= 0 {
		workers = runtime.NumCPU() * 4
		if workers > 64 {
			workers = 64
		}
	}
	sem := make(chan struct{}, workers)
	var wg sync.WaitGroup
	var walk func(dir string)
	walk = func(dir string) {
		if ctx.Err() != nil {
			return
		}
		entries, err := os.ReadDir(dir)
		if err != nil {
			atomic.AddInt64(&st.Skipped, 1)
			return
		}
		atomic.AddInt64(&st.Dirs, 1)
		for _, e := range entries {
			p := filepath.Join(dir, e.Name())
			t := e.Type()
			if t&(fs.ModeSymlink|fs.ModeIrregular|fs.ModeDevice|fs.ModeNamedPipe|fs.ModeSocket) != 0 {
				continue
			}
			if e.IsDir() {
				if opt.SkipDir != nil && opt.SkipDir(p, e.Name()) {
					continue
				}
				select {
				case sem <- struct{}{}:
					wg.Add(1)
					go func(p string) {
						defer func() { <-sem; wg.Done() }()
						walk(p)
					}(p)
				default:
					walk(p)
				}
				continue
			}
			fi, err := e.Info()
			if err != nil {
				continue
			}
			size := fi.Size()
			if IsCloudPlaceholder(fi) {
				size = 0
			}
			atomic.AddInt64(&st.Bytes, size)
			atomic.AddInt64(&st.Files, 1)
			if opt.OnFile != nil {
				opt.OnFile(p, size, fi.ModTime())
			}
		}
	}
	if fi, err := os.Stat(root); err == nil && !fi.IsDir() {
		st.Files, st.Bytes = 1, fi.Size()
		if opt.OnFile != nil {
			opt.OnFile(root, fi.Size(), fi.ModTime())
		}
		return st
	}
	walk(root)
	wg.Wait()
	return st
}

// DirSize is a convenience wrapper returning total bytes and file count.
func DirSize(ctx context.Context, root string) (int64, int64) {
	st := Walk(ctx, root, WalkOptions{})
	return st.Bytes, st.Files
}

// SizeOlderThan sums files under root last modified before cutoff.
func SizeOlderThan(ctx context.Context, root string, cutoff time.Time) (int64, int64) {
	var b, n int64
	Walk(ctx, root, WalkOptions{OnFile: func(_ string, size int64, mod time.Time) {
		if mod.Before(cutoff) {
			atomic.AddInt64(&b, size)
			atomic.AddInt64(&n, 1)
		}
	}})
	return b, n
}

// SizedPath is a path with a size.
type SizedPath struct {
	Path  string    `json:"path"`
	Bytes int64     `json:"bytes"`
	Files int64     `json:"files,omitempty"`
	Mod   time.Time `json:"modified,omitempty"`
}

type sizeHeap []SizedPath

func (h sizeHeap) Len() int           { return len(h) }
func (h sizeHeap) Less(i, j int) bool { return h[i].Bytes < h[j].Bytes }
func (h sizeHeap) Swap(i, j int)      { h[i], h[j] = h[j], h[i] }
func (h *sizeHeap) Push(x any)        { *h = append(*h, x.(SizedPath)) }
func (h *sizeHeap) Pop() any {
	old := *h
	x := old[len(old)-1]
	*h = old[:len(old)-1]
	return x
}

// TopN keeps the N largest items seen, safe for concurrent Offer calls.
type TopN struct {
	n    int
	mu   sync.Mutex
	h    sizeHeap
	min  atomic.Int64
	full atomic.Bool
}

// NewTopN creates a collector for the n largest entries.
func NewTopN(n int) *TopN { return &TopN{n: n} }

// Offer considers one item.
func (t *TopN) Offer(p SizedPath) {
	if t.full.Load() && p.Bytes <= t.min.Load() {
		return
	}
	t.mu.Lock()
	defer t.mu.Unlock()
	if len(t.h) < t.n {
		heap.Push(&t.h, p)
	} else if p.Bytes > t.h[0].Bytes {
		t.h[0] = p
		heap.Fix(&t.h, 0)
	}
	if len(t.h) >= t.n {
		t.min.Store(t.h[0].Bytes)
		t.full.Store(true)
	}
}

// Sorted returns the items largest first.
func (t *TopN) Sorted() []SizedPath {
	t.mu.Lock()
	defer t.mu.Unlock()
	out := append([]SizedPath(nil), t.h...)
	sort.Slice(out, func(i, j int) bool { return out[i].Bytes > out[j].Bytes })
	return out
}

// ChildSizes measures each immediate child of dir in parallel, largest first.
// Loose files directly inside dir are grouped as "<files>".
func ChildSizes(ctx context.Context, dir string) []SizedPath {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil
	}
	var mu sync.Mutex
	var out []SizedPath
	var loose SizedPath
	loose.Path = filepath.Join(dir, "<files>")
	var wg sync.WaitGroup
	sem := make(chan struct{}, 8)
	for _, e := range entries {
		p := filepath.Join(dir, e.Name())
		if e.Type()&(fs.ModeSymlink|fs.ModeIrregular) != 0 {
			continue
		}
		if !e.IsDir() {
			if fi, err := e.Info(); err == nil && !IsCloudPlaceholder(fi) {
				loose.Bytes += fi.Size()
				loose.Files++
			}
			continue
		}
		wg.Add(1)
		sem <- struct{}{}
		go func(p string) {
			defer func() { <-sem; wg.Done() }()
			st := Walk(ctx, p, WalkOptions{Workers: 16})
			mu.Lock()
			out = append(out, SizedPath{Path: p, Bytes: st.Bytes, Files: st.Files})
			mu.Unlock()
		}(p)
	}
	wg.Wait()
	if loose.Files > 0 {
		out = append(out, loose)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Bytes > out[j].Bytes })
	return out
}
