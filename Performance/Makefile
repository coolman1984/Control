# Cross-compile from Linux/macOS:  make
.PHONY: all test windows
all: test windows
test:
	go vet ./... && GOOS=windows go vet ./... && go test ./...
windows:
	mkdir -p dist
	CGO_ENABLED=0 GOOS=windows GOARCH=amd64 go build -trimpath -ldflags "-s -w" -o dist/winsight.exe ./cmd/winsight
	CGO_ENABLED=0 GOOS=windows GOARCH=arm64 go build -trimpath -ldflags "-s -w" -o dist/winsight-arm64.exe ./cmd/winsight
