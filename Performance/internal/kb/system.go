package kb

import "github.com/coolman1984/performance/internal/core"

// Bloat lists preinstalled / promoted Store apps that most people never use.
// Every one of them can be reinstalled from the Microsoft Store.
var Bloat = map[string]string{
	"king.com.CandyCrushSaga":                "Candy Crush (promoted game)",
	"king.com.CandyCrushSodaSaga":            "Candy Crush Soda (promoted game)",
	"king.com.BubbleWitch3Saga":              "Bubble Witch (promoted game)",
	"Microsoft.BingNews":                     "Microsoft News",
	"Microsoft.BingSearch":                   "Bing Search app",
	"Microsoft.GetHelp":                      "Get Help",
	"Microsoft.Getstarted":                   "Tips",
	"Microsoft.MicrosoftSolitaireCollection": "Solitaire (ads)",
	"Microsoft.MixedReality.Portal":          "Mixed Reality Portal",
	"Microsoft.People":                       "People",
	"Microsoft.SkypeApp":                     "Skype (retired)",
	"Microsoft.WindowsFeedbackHub":           "Feedback Hub",
	"Microsoft.ZuneVideo":                    "Movies & TV",
	"Microsoft.3DBuilder":                    "3D Builder",
	"Microsoft.Microsoft3DViewer":            "3D Viewer",
	"Microsoft.Print3D":                      "Print 3D",
	"Microsoft.MicrosoftOfficeHub":           "Office hub / Microsoft 365 promo",
	"Clipchamp.Clipchamp":                    "Clipchamp video editor",
	"Microsoft.PowerAutomateDesktop":         "Power Automate",
	"MicrosoftTeams":                         "Teams (personal)",
	"Microsoft.549981C3F5F10":                "Cortana (retired)",
	"Microsoft.WindowsMaps":                  "Maps",
	"Microsoft.Messaging":                    "Messaging",
	"Microsoft.OneConnect":                   "Mobile Plans",
	"Microsoft.Wallet":                       "Wallet",
	"Microsoft.Windows.DevHome":              "Dev Home (retired)",
	"Microsoft.XboxApp":                      "Xbox Console Companion (old)",
	"Microsoft.MicrosoftJournal":             "Journal",
	"Microsoft.Todos":                        "To Do",
	"Microsoft.BingWeather":                  "Weather",
	"Disney.37853FC22B2CE":                   "Disney+ (promoted)",
	"BytedancePte.Ltd.TikTok":                "TikTok (promoted)",
	"AmazonVideo.PrimeVideo":                 "Prime Video (promoted)",
	"Facebook.Facebook":                      "Facebook (promoted)",
	"Facebook.InstagramBeta":                 "Instagram (promoted)",
	"5319275A.WhatsAppDesktop":               "WhatsApp (promoted)",
	"SpotifyAB.SpotifyMusic":                 "Spotify (promoted)",
}

// EssentialService should exist and not be disabled; Run means it should be running.
type EssentialService struct {
	Name, Title string
	Start       int // default HKLM ...\Services\<name>\Start value: 2 auto, 3 manual
	Run         bool
	Why         string
}

// EssentialServices are the ones whose absence breaks Windows in confusing ways.
var EssentialServices = []EssentialService{
	{"Winmgmt", "Windows Management Instrumentation", 2, true, "Many apps and Windows tools stop working without WMI."},
	{"EventLog", "Windows Event Log", 2, true, "No logs means no diagnostics; some apps refuse to start."},
	{"RpcSs", "Remote Procedure Call", 2, true, "Core of Windows; nothing works without it."},
	{"Dhcp", "DHCP Client", 2, true, "Needed to get an IP address automatically (no internet without it)."},
	{"Dnscache", "DNS Client", 2, true, "Resolves web addresses; disabling breaks or slows browsing."},
	{"nsi", "Network Store Interface", 2, true, "Network icon shows 'no internet' without it."},
	{"BFE", "Base Filtering Engine", 2, true, "Firewall, VPNs and IPsec depend on it."},
	{"mpssvc", "Windows Defender Firewall", 2, true, "Your firewall."},
	{"CryptSvc", "Cryptographic Services", 2, true, "Needed for updates, Store and signed apps."},
	{"Schedule", "Task Scheduler", 2, true, "Maintenance, updates and many apps rely on it."},
	{"Power", "Power", 2, true, "Power plans and sleep."},
	{"ProfSvc", "User Profile Service", 2, true, "Loading user profiles at sign-in."},
	{"LanmanWorkstation", "Workstation", 2, true, "Network shares and mapped drives."},
	{"AudioSrv", "Windows Audio", 2, true, "No sound without it."},
	{"AudioEndpointBuilder", "Windows Audio Endpoint Builder", 2, true, "No sound devices without it."},
	{"Themes", "Themes", 2, true, "Visual styles; breaks the look of Windows when off."},
	{"UserManager", "User Manager", 2, true, "Start menu and sign-in depend on it."},
	{"EventSystem", "COM+ Event System", 2, true, "Many system notifications."},
	{"SENS", "System Event Notification", 2, true, "Logon/network events for apps."},
	{"wscsvc", "Security Center", 2, true, "Reports antivirus/firewall status."},
	{"DPS", "Diagnostic Policy Service", 2, false, "Troubleshooters and problem detection."},
	{"BITS", "Background Intelligent Transfer", 3, false, "Windows Update and Store downloads."},
	{"wuauserv", "Windows Update", 3, false, "Security updates. Disabled updates = unpatched PC."},
	{"TrustedInstaller", "Windows Modules Installer", 3, false, "Installing updates and Windows features; sfc/DISM need it."},
	{"msiserver", "Windows Installer", 3, false, "Installing and uninstalling .msi programs."},
	{"W32Time", "Windows Time", 3, false, "Keeps the clock right (wrong clock breaks HTTPS)."},
	{"AppXSvc", "AppX Deployment", 3, false, "Installing/updating Store apps."},
	{"ClipSVC", "Client License Service", 3, false, "Store app licences; apps won't open without it."},
	{"InstallService", "Microsoft Store Install Service", 3, false, "Store installs."},
	{"StateRepository", "State Repository", 3, false, "Start menu and Store apps."},
}

// ServiceAdvice are services worth changing on a typical home PC.
type ServiceAdvice struct {
	Name, Title, Why string
	Want             int // 3 manual, 4 disabled
	Risk             core.Risk
	Sev              core.Severity
}

// ServiceAdvices is consulted by the `services` tool.
var ServiceAdvices = []ServiceAdvice{
	{"RemoteRegistry", "Remote Registry", "Lets other computers edit your registry. Should be off on a home PC.", 4, core.Safe, core.Medium},
	{"DiagTrack", "Connected User Experiences and Telemetry", "Sends usage telemetry to Microsoft and writes to disk continuously.", 4, core.Moderate, core.Low},
	{"RetailDemo", "Retail Demo", "Only for store display PCs.", 4, core.Safe, core.Low},
	{"MapsBroker", "Downloaded Maps Manager", "Keeps offline maps updated; few people use them.", 3, core.Safe, core.Low},
	{"Fax", "Fax", "Nobody has a fax modem any more.", 4, core.Safe, core.Low},
	{"WMPNetworkSvc", "Windows Media Player Network Sharing", "Shares your media library on the network.", 4, core.Safe, core.Low},
	{"lfsvc", "Geolocation", "Only needed for location-aware apps.", 3, core.Safe, core.Info},
}

// KnownUpdaters are third-party auto-start services that only check for updates.
var KnownUpdaters = map[string]string{
	"gupdate": "Google Update", "gupdatem": "Google Update", "GoogleUpdaterService": "Google Updater", "GoogleUpdaterInternalService": "Google Updater",
	"edgeupdate": "Edge Update", "edgeupdatem": "Edge Update", "MicrosoftEdgeElevationService": "Edge elevation",
	"AdobeARMservice": "Adobe Acrobat Update", "AdobeUpdateService": "Adobe Update", "AGSService": "Adobe Genuine Software", "AGMService": "Adobe Genuine Monitor",
	"brave": "Brave Update", "bravem": "Brave Update", "BraveElevationService": "Brave elevation",
	"MozillaMaintenance": "Firefox maintenance", "DbxSvc": "Dropbox", "DropboxUpdate": "Dropbox Update",
	"ZoomCptService": "Zoom", "ClickToRunSvc": "Office Click-to-Run", "Razer Game Scanner Service": "Razer",
	"LGHUBUpdaterService": "Logitech G HUB updater", "CCleanerPerformanceOptimizerService": "CCleaner",
	"AvastWscReporter": "Avast", "SteelSeriesUpdateService": "SteelSeries updater", "NahimicService": "Nahimic audio",
}

// CriticalFiles live under %SystemRoot% and must exist.
var CriticalFiles = []string{
	`explorer.exe`, `regedit.exe`, `notepad.exe`,
	`System32\ntoskrnl.exe`, `System32\hal.dll`, `System32\winload.exe`, `System32\winload.efi`,
	`System32\cmd.exe`, `System32\svchost.exe`, `System32\services.exe`, `System32\lsass.exe`,
	`System32\winlogon.exe`, `System32\csrss.exe`, `System32\smss.exe`, `System32\userinit.exe`,
	`System32\rundll32.exe`, `System32\conhost.exe`, `System32\taskmgr.exe`, `System32\mmc.exe`,
	`System32\msconfig.exe`, `System32\sfc.exe`, `System32\Dism.exe`, `System32\control.exe`,
	`System32\msiexec.exe`, `System32\SystemSettings.exe`, `ImmersiveControlPanel\SystemSettings.exe`,
	`System32\WindowsPowerShell\v1.0\powershell.exe`, `System32\drivers\etc\hosts`,
	`System32\config\SYSTEM`, `System32\config\SOFTWARE`,
}

// Tweak is a registry setting with a recommended value.
type Tweak struct {
	ID, Group, Title, Why string
	Key, Name             string // PowerShell registry path (HKCU:\ / HKLM:\)
	Type                  string // DWord | String
	Want                  string
	Missing               string // what Windows behaves like when the value is absent
	Risk                  core.Risk
	Sev                   core.Severity
	After                 string // what's needed for it to apply
}

// Tweaks is consulted by the `tweaks` tool.
var Tweaks = []Tweak{
	{ID: "file-extensions", Group: "security", Title: "Show file extensions", Why: "Hidden extensions let 'invoice.pdf.exe' pass as a PDF. Showing them is the #1 anti-malware habit.",
		Key: `HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced`, Name: "HideFileExt", Type: "DWord", Want: "0", Missing: "1", Risk: core.Safe, Sev: core.Medium, After: "Reopen Explorer windows"},
	{ID: "start-web-search", Group: "speed", Title: "Stop Start menu sending searches to Bing", Why: "Every keystroke in Start is sent to Bing; local search becomes slower and less relevant.",
		Key: `HKCU:\Software\Policies\Microsoft\Windows\Explorer`, Name: "DisableSearchBoxSuggestions", Type: "DWord", Want: "1", Missing: "0", Risk: core.Safe, Sev: core.Low, After: "Sign out or restart Explorer"},
	{ID: "silent-app-installs", Group: "privacy", Title: "Stop silent installs of promoted apps", Why: "Windows quietly installs sponsored games/apps (Candy Crush, TikTok…) that take space and run in background.",
		Key: `HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager`, Name: "SilentInstalledAppsEnabled", Type: "DWord", Want: "0", Missing: "1", Risk: core.Safe, Sev: core.Low},
	{ID: "start-suggestions", Group: "privacy", Title: "Turn off Start menu ads/suggestions", Why: "Suggested apps in Start are advertisements.",
		Key: `HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager`, Name: "SubscribedContent-338388Enabled", Type: "DWord", Want: "0", Missing: "1", Risk: core.Safe, Sev: core.Info},
	{ID: "tips-notifications", Group: "privacy", Title: "Turn off 'tips and suggestions' notifications", Why: "Reduces nag notifications and background content downloads.",
		Key: `HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager`, Name: "SubscribedContent-338389Enabled", Type: "DWord", Want: "0", Missing: "1", Risk: core.Safe, Sev: core.Info},
	{ID: "advertising-id", Group: "privacy", Title: "Disable advertising ID", Why: "Apps use it to track you across apps for ads.",
		Key: `HKCU:\Software\Microsoft\Windows\CurrentVersion\AdvertisingInfo`, Name: "Enabled", Type: "DWord", Want: "0", Missing: "1", Risk: core.Safe, Sev: core.Low},
	{ID: "tailored-experiences", Group: "privacy", Title: "Disable tailored experiences", Why: "Stops Microsoft using diagnostic data for personalised tips and ads.",
		Key: `HKCU:\Software\Microsoft\Windows\CurrentVersion\Privacy`, Name: "TailoredExperiencesWithDiagnosticDataEnabled", Type: "DWord", Want: "0", Missing: "1", Risk: core.Safe, Sev: core.Info},
	{ID: "telemetry-level", Group: "privacy", Title: "Limit telemetry to required data", Why: "Optional diagnostic data sends more usage details and keeps DiagTrack busy.",
		Key: `HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection`, Name: "AllowTelemetry", Type: "DWord", Want: "1", Missing: "3", Risk: core.Safe, Sev: core.Low},
	{ID: "activity-history", Group: "privacy", Title: "Disable activity history", Why: "Windows records apps/files you open (Timeline).",
		Key: `HKLM:\SOFTWARE\Policies\Microsoft\Windows\System`, Name: "PublishUserActivities", Type: "DWord", Want: "0", Missing: "1", Risk: core.Safe, Sev: core.Info},
	{ID: "widgets", Group: "speed", Title: "Turn off Widgets (news feed)", Why: "The Widgets board keeps a WebView2 process (100–300 MB RAM) alive for news you rarely read.",
		Key: `HKLM:\SOFTWARE\Policies\Microsoft\Dsh`, Name: "AllowNewsAndInterests", Type: "DWord", Want: "0", Missing: "1", Risk: core.Safe, Sev: core.Low, After: "Sign out"},
	{ID: "game-dvr", Group: "speed", Title: "Turn off background game recording", Why: "Game DVR continuously records gameplay in the background and costs FPS.",
		Key: `HKCU:\System\GameConfigStore`, Name: "GameDVR_Enabled", Type: "DWord", Want: "0", Missing: "1", Risk: core.Safe, Sev: core.Low},
	{ID: "game-mode", Group: "speed", Title: "Keep Game Mode on", Why: "Game Mode stops Windows Update and background work from stealing CPU during games.",
		Key: `HKCU:\Software\Microsoft\GameBar`, Name: "AutoGameModeEnabled", Type: "DWord", Want: "1", Missing: "1", Risk: core.Safe, Sev: core.Info},
	{ID: "menu-delay", Group: "speed", Title: "Snappier menus", Why: "Windows waits 400 ms before opening sub-menus; 100 ms feels instant.",
		Key: `HKCU:\Control Panel\Desktop`, Name: "MenuShowDelay", Type: "String", Want: "100", Missing: "400", Risk: core.Safe, Sev: core.Info, After: "Sign out"},
	{ID: "background-apps", Group: "speed", Title: "Stop Store apps running in background", Why: "Store apps keep running to show live tiles and notifications, using RAM and battery.",
		Key: `HKCU:\Software\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications`, Name: "GlobalUserDisabled", Type: "DWord", Want: "1", Missing: "0", Risk: core.Moderate, Sev: core.Info},
	{ID: "fast-startup", Group: "health", Title: "Disable Fast Startup", Why: "Fast Startup half-hibernates the kernel: drivers never fully reset, uptime never resets, updates and dual-boot misbehave. A real restart fixes more problems.",
		Key: `HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power`, Name: "HiberbootEnabled", Type: "DWord", Want: "0", Missing: "1", Risk: core.Moderate, Sev: core.Low},
	{ID: "long-paths", Group: "dev", Title: "Allow long file paths (>260 chars)", Why: "Deep node_modules / git repos fail to copy or delete without it.",
		Key: `HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem`, Name: "LongPathsEnabled", Type: "DWord", Want: "1", Missing: "0", Risk: core.Safe, Sev: core.Info},
	{ID: "copilot", Group: "privacy", Title: "Turn off Windows Copilot sidebar", Why: "Removes the Copilot sidebar and its background process.",
		Key: `HKCU:\Software\Policies\Microsoft\Windows\WindowsCopilot`, Name: "TurnOffWindowsCopilot", Type: "DWord", Want: "1", Missing: "0", Risk: core.Safe, Sev: core.Info},
	{ID: "recall", Group: "privacy", Title: "Disable Recall snapshots", Why: "On Copilot+ PCs Recall screenshots your screen every few seconds (privacy + disk).",
		Key: `HKCU:\Software\Policies\Microsoft\Windows\WindowsAI`, Name: "DisableAIDataAnalysis", Type: "DWord", Want: "1", Missing: "0", Risk: core.Safe, Sev: core.Info},
}
