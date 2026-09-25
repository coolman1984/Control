// Package kb is WinSight's built-in knowledge: where Windows hides junk,
// what event IDs and crash codes mean, which services and tweaks matter.
// Keeping it here means an agent never has to research these facts again.
package kb

import "github.com/coolman1984/performance/internal/core"

// JunkSpot is a location whose contents Windows or an app regenerates.
type JunkSpot struct {
	ID            string
	Title         string
	Paths         []string // may contain {local}, %VAR% and * globs
	Why           string
	Risk          core.Risk
	Admin         bool
	OlderThanDays int      // only files older than this are removed (0 = all)
	Cmd           []string // preferred cleaner instead of deleting files
	Group         string   // system | browser | apps | dev
}

// JunkSpots is the catalogue scanned by the `junk` and `devcache` tools.
var JunkSpots = []JunkSpot{
	// ---- Windows itself
	{ID: "user-temp", Group: "system", Title: "Your temp folder", Paths: []string{"{temp}"}, OlderThanDays: 2, Risk: core.Safe,
		Why: "Apps leave installers, extracted archives and logs here and rarely clean up. Files older than 2 days are not in use."},
	{ID: "windows-temp", Group: "system", Title: "Windows temp folder", Paths: []string{"{windows}\\Temp"}, OlderThanDays: 2, Risk: core.Safe, Admin: true,
		Why: "System-wide temp used by installers and services."},
	{ID: "wu-download", Group: "system", Title: "Windows Update download cache", Paths: []string{"{windows}\\SoftwareDistribution\\Download"}, Risk: core.Safe, Admin: true,
		Why: "Already-installed update packages. Windows re-downloads anything it still needs."},
	{ID: "delivery-opt", Group: "system", Title: "Delivery Optimization cache", Paths: []string{"{windows}\\ServiceProfiles\\NetworkService\\AppData\\Local\\Microsoft\\Windows\\DeliveryOptimization\\Cache"}, Risk: core.Safe, Admin: true,
		Why: "Update pieces kept to share with other PCs. Safe to purge.",
		Cmd: []string{"powershell", "-NoProfile", "-Command", "Delete-DeliveryOptimizationCache -Force"}},
	{ID: "crash-dumps", Group: "system", Title: "Crash dumps", Paths: []string{"{local}\\CrashDumps", "{windows}\\Minidump", "{windows}\\MEMORY.DMP", "{windows}\\LiveKernelReports"}, Risk: core.Safe, Admin: true,
		Why: "Memory snapshots from crashes. Only useful if you are debugging a crash (run `crashes` first to read them)."},
	{ID: "wer", Group: "system", Title: "Windows Error Reporting archives", Paths: []string{"{programdata}\\Microsoft\\Windows\\WER\\ReportArchive", "{programdata}\\Microsoft\\Windows\\WER\\ReportQueue", "{local}\\Microsoft\\Windows\\WER"}, Risk: core.Safe, Admin: true,
		Why: "Copies of crash reports already sent (or never to be sent) to Microsoft."},
	{ID: "thumbcache", Group: "system", Title: "Thumbnail & icon cache", Paths: []string{"{local}\\Microsoft\\Windows\\Explorer"}, Risk: core.Safe,
		Why: "Explorer rebuilds thumbnails on demand. Clearing also fixes wrong/blank icons (locked files are skipped)."},
	{ID: "cbs-logs", Group: "system", Title: "Windows servicing logs", Paths: []string{"{windows}\\Logs\\CBS", "{windows}\\Logs\\DISM", "{windows}\\Logs\\MoSetup", "{windows}\\Panther"}, OlderThanDays: 14, Risk: core.Safe, Admin: true,
		Why: "Old component-servicing and setup logs; can grow to gigabytes after failed updates."},
	{ID: "inet-cache", Group: "system", Title: "Legacy internet cache (INetCache)", Paths: []string{"{local}\\Microsoft\\Windows\\INetCache"}, Risk: core.Safe,
		Why: "Cache used by Office, old IE components and web views."},
	{ID: "d3d-shader", Group: "system", Title: "DirectX shader cache", Paths: []string{"{local}\\D3DSCache", "{local}\\NVIDIA\\DXCache", "{local}\\NVIDIA\\GLCache", "{local}\\AMD\\DxCache", "{local}\\AMD\\GLCache", "{local}\\AMD\\DxcCache"}, Risk: core.Safe,
		Why: "Compiled GPU shaders. Rebuilt automatically; first game launch may stutter briefly."},
	{ID: "store-cache", Group: "apps", Title: "Microsoft Store app caches", Paths: []string{"{local}\\Packages\\*\\AC\\INetCache", "{local}\\Packages\\*\\LocalCache\\Local\\Microsoft\\Windows\\INetCache"}, Risk: core.Safe,
		Why: "Per-app web caches of Store apps."},
	{ID: "old-installers-cache", Group: "system", Title: "Leftover setup files", Paths: []string{"{sysdrive}\\$WINDOWS.~BT", "{sysdrive}\\$WINDOWS.~WS", "{sysdrive}\\$GetCurrent", "{sysdrive}\\ESD"}, Risk: core.Moderate, Admin: true,
		Why: "Temporary Windows upgrade files. Use Disk Cleanup (cleanmgr) to remove them properly.",
		Cmd: []string{"cleanmgr.exe", "/verylowdisk"}},

	// ---- Browsers
	{ID: "chrome-cache", Group: "browser", Title: "Chrome cache", Paths: []string{"{local}\\Google\\Chrome\\User Data\\*\\Cache", "{local}\\Google\\Chrome\\User Data\\*\\Code Cache", "{local}\\Google\\Chrome\\User Data\\*\\GPUCache", "{local}\\Google\\Chrome\\User Data\\*\\Service Worker\\CacheStorage"}, Risk: core.Safe,
		Why: "Web page cache. Logins, history and bookmarks are NOT touched. Close Chrome first for a full clean."},
	{ID: "edge-cache", Group: "browser", Title: "Edge cache", Paths: []string{"{local}\\Microsoft\\Edge\\User Data\\*\\Cache", "{local}\\Microsoft\\Edge\\User Data\\*\\Code Cache", "{local}\\Microsoft\\Edge\\User Data\\*\\GPUCache", "{local}\\Microsoft\\Edge\\User Data\\*\\Service Worker\\CacheStorage"}, Risk: core.Safe,
		Why: "Web page cache. Logins, history and favourites are NOT touched."},
	{ID: "brave-cache", Group: "browser", Title: "Brave cache", Paths: []string{"{local}\\BraveSoftware\\Brave-Browser\\User Data\\*\\Cache", "{local}\\BraveSoftware\\Brave-Browser\\User Data\\*\\Code Cache"}, Risk: core.Safe,
		Why: "Web page cache."},
	{ID: "firefox-cache", Group: "browser", Title: "Firefox cache", Paths: []string{"{local}\\Mozilla\\Firefox\\Profiles\\*\\cache2"}, Risk: core.Safe,
		Why: "Web page cache. Profile data lives in Roaming and is NOT touched."},
	{ID: "opera-cache", Group: "browser", Title: "Opera cache", Paths: []string{"{local}\\Opera Software\\*\\Cache"}, Risk: core.Safe,
		Why: "Web page cache."},

	// ---- Apps
	{ID: "teams-cache", Group: "apps", Title: "Microsoft Teams cache", Paths: []string{"{roaming}\\Microsoft\\Teams\\Cache", "{roaming}\\Microsoft\\Teams\\Service Worker\\CacheStorage", "{local}\\Packages\\MSTeams_8wekyb3d8bbwe\\LocalCache\\Microsoft\\MSTeams\\EBWebView\\Default\\Cache"}, Risk: core.Safe,
		Why: "Teams keeps a large web cache; clearing also fixes many Teams glitches."},
	{ID: "discord-cache", Group: "apps", Title: "Discord cache", Paths: []string{"{roaming}\\discord\\Cache", "{roaming}\\discord\\Code Cache", "{roaming}\\discord\\GPUCache"}, Risk: core.Safe, Why: "Media and code cache."},
	{ID: "slack-cache", Group: "apps", Title: "Slack cache", Paths: []string{"{roaming}\\Slack\\Cache", "{roaming}\\Slack\\Service Worker\\CacheStorage"}, Risk: core.Safe, Why: "Media and code cache."},
	{ID: "spotify-cache", Group: "apps", Title: "Spotify cache", Paths: []string{"{local}\\Spotify\\Data", "{local}\\Packages\\SpotifyAB.SpotifyMusic_zpdnekdrzrea0\\LocalCache\\Spotify\\Data"}, Risk: core.Safe, Why: "Streamed songs cache (downloads for offline are elsewhere)."},
	{ID: "steam-cache", Group: "apps", Title: "Steam web & shader cache", Paths: []string{"{local}\\Steam\\htmlcache", "%ProgramFiles(x86)%\\Steam\\appcache\\httpcache"}, Risk: core.Safe, Why: "Store page cache."},
	{ID: "vscode-cache", Group: "dev", Title: "VS Code caches", Paths: []string{"{roaming}\\Code\\Cache", "{roaming}\\Code\\CachedData", "{roaming}\\Code\\CachedExtensionVSIXs", "{roaming}\\Code\\Code Cache", "{roaming}\\Code\\logs"}, Risk: core.Safe, Why: "Editor caches and logs; settings and extensions untouched."},

	// ---- Developer caches (the `devcache` tool)
	{ID: "npm", Group: "dev", Title: "npm cache", Paths: []string{"{local}\\npm-cache", "{roaming}\\npm-cache"}, Risk: core.Safe, Why: "Downloaded packages; re-fetched on demand.", Cmd: []string{"npm", "cache", "clean", "--force"}},
	{ID: "yarn", Group: "dev", Title: "Yarn cache", Paths: []string{"{local}\\Yarn\\Cache"}, Risk: core.Safe, Why: "Downloaded packages.", Cmd: []string{"yarn", "cache", "clean"}},
	{ID: "pnpm", Group: "dev", Title: "pnpm store", Paths: []string{"{local}\\pnpm\\store", "{local}\\pnpm-store"}, Risk: core.Safe, Why: "Content-addressed package store; prune removes unreferenced packages.", Cmd: []string{"pnpm", "store", "prune"}},
	{ID: "pip", Group: "dev", Title: "pip cache", Paths: []string{"{local}\\pip\\Cache"}, Risk: core.Safe, Why: "Downloaded wheels.", Cmd: []string{"pip", "cache", "purge"}},
	{ID: "uv", Group: "dev", Title: "uv cache", Paths: []string{"{local}\\uv\\cache"}, Risk: core.Safe, Why: "Python package cache.", Cmd: []string{"uv", "cache", "clean"}},
	{ID: "conda", Group: "dev", Title: "Conda package cache", Paths: []string{"{user}\\anaconda3\\pkgs", "{user}\\miniconda3\\pkgs", "{local}\\conda\\conda\\pkgs"}, Risk: core.Safe, Why: "Tarballs of installed packages.", Cmd: []string{"conda", "clean", "--all", "-y"}},
	{ID: "nuget", Group: "dev", Title: "NuGet caches", Paths: []string{"{user}\\.nuget\\packages", "{local}\\NuGet\\v3-cache", "{local}\\NuGet\\plugins-cache"}, Risk: core.Safe, Why: ".NET package caches.", Cmd: []string{"dotnet", "nuget", "locals", "all", "--clear"}},
	{ID: "gradle", Group: "dev", Title: "Gradle caches", Paths: []string{"{user}\\.gradle\\caches", "{user}\\.gradle\\wrapper\\dists"}, Risk: core.Safe, Why: "Build dependency caches and wrapper distributions."},
	{ID: "maven", Group: "dev", Title: "Maven repository", Paths: []string{"{user}\\.m2\\repository"}, Risk: core.Safe, Why: "Downloaded Java artifacts."},
	{ID: "cargo", Group: "dev", Title: "Cargo registry cache", Paths: []string{"{user}\\.cargo\\registry\\cache", "{user}\\.cargo\\registry\\src", "{user}\\.cargo\\git\\checkouts"}, Risk: core.Safe, Why: "Downloaded crates."},
	{ID: "go", Group: "dev", Title: "Go build & module cache", Paths: []string{"{local}\\go-build", "{user}\\go\\pkg\\mod"}, Risk: core.Safe, Why: "Build cache and modules.", Cmd: []string{"go", "clean", "-cache", "-modcache"}},
	{ID: "android", Group: "dev", Title: "Android build caches", Paths: []string{"{user}\\.android\\cache", "{user}\\.android\\build-cache"}, Risk: core.Safe, Why: "Android SDK build cache."},
	{ID: "jetbrains", Group: "dev", Title: "JetBrains IDE caches & logs", Paths: []string{"{local}\\JetBrains\\*\\caches", "{local}\\JetBrains\\*\\log", "{local}\\JetBrains\\*\\index"}, Risk: core.Safe, Why: "Indexes are rebuilt when the IDE opens a project."},
	{ID: "docker-wsl", Group: "dev", Title: "Docker Desktop data (reclaim with prune)", Paths: []string{"{local}\\Docker\\wsl"}, Risk: core.Moderate, Why: "Docker images and volumes live in a virtual disk that never shrinks by itself.",
		Cmd: []string{"docker", "system", "prune", "-af"}},
	{ID: "vs-cache", Group: "dev", Title: "Visual Studio component cache", Paths: []string{"{programdata}\\Microsoft\\VisualStudio\\Packages"}, Risk: core.Moderate, Admin: true, Why: "Installer package cache; removing it means repairs need a download."},
	{ID: "ms-playwright", Group: "dev", Title: "Playwright browsers", Paths: []string{"{local}\\ms-playwright"}, Risk: core.Safe, Why: "Downloaded test browsers; reinstall with `npx playwright install`."},
	{ID: "huggingface", Group: "dev", Title: "Hugging Face model cache", Paths: []string{"{user}\\.cache\\huggingface"}, Risk: core.Moderate, Why: "Downloaded AI models; can be tens of GB. Re-downloaded on next use."},
	{ID: "ollama", Group: "dev", Title: "Ollama models", Paths: []string{"{user}\\.ollama\\models"}, Risk: core.Moderate, Why: "Local LLM weights. Remove unused ones with `ollama rm <model>`."},
}
