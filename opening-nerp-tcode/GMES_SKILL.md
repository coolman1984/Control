---
name: gmes-automation
description: Automates Samsung G-MES (http://seegmes4.sec.samsung.net, a Nexacro application) through Chrome DevTools Protocol - signs in unattended with a DPAPI-stored Knox credential via AD SSO, clears the Notice popups, opens work screens, sets filter dates and Division, runs an Inquiry, reads whole result sets out of the Nexacro data layer, and exports with GMES's own Excel download. Use whenever asked to open GMES, log in to GMES, run or schedule a GMES report, read GMES data, or export a GMES screen to Excel.
---

# G-MES automation

G-MES is a **Nexacro** application, not SAP. It renders real DOM elements
with stable, meaningful ids, which makes it more tractable than N-ERP's
WebGUI — but it hides its data behind a JavaScript object model, and several
of its behaviours will silently produce wrong results if taken at face
value.

Read [CLAUDE.md](CLAUDE.md) before changing anything here, and record what
you learn in [HISTORY.md](HISTORY.md).

## Current supported engine

**As restored in HISTORY.md Phase 57**, the production core is the flat
legacy engine:
`gmes_core.py`, `gmes_login.py`, `gmes_common.py`, `gmes_open_screen.py`,
`gmes_data.py`, `gmes_profile.py`, `cdp_common.py`.

`src/gmes/` was removed after capability rescue. Do not recreate or run a
second G-MES implementation. The donor is recoverable only from Git history
at `59eb838` / `archive/standalone-gmes-before-removal`.

Rationale and capability decisions: `ARCHITECTURE.md` and
`docs/history/CAPABILITY_RESCUE_MAP.md`.

## مسارات التشغيل

يوجد محرك واحد: الملفات المسطحة في جذر المشروع. المدخل الأساسي هو
`GMES_Workflow.bat`: بدون معاملات يشغّل `run_gmes_workflow.py`، ومع معاملات
يشغّل `gmes_report.py run %*`.

```powershell
python -m pip install -r requirements.txt
python gmes_credentials.py set
.\GMES_Workflow.bat
.\GMES_Workflow.bat P1112UM00 --division VD --from 20260909 --to 20260909 --verify planYmd
python gmes_report.py run P1112UM00 --division VD --from 20260909 --to 20260909 --verify planYmd
```

**فخ شائع:** لا تسبق كود الشاشة بكلمة `run` عند استعمال `GMES_Workflow.bat`
— الملف نفسه يضيف `run` تلقائياً. كتابتها بنفسك (`GMES_Workflow.bat run
P1112UM00 ...`) تجعل `run` يُفهم على أنه كود شاشة غير موجود، فيفشل البحث
ثم يفشل التقرير الفعلي تبعاً له. `run` مطلوبة فقط مع `python gmes_report.py`
المباشر (الأمر الثالث أعلاه)، أبداً مع `.\GMES_Workflow.bat` (الأمر الثاني).

يمكن استعمال `data forms` و`data read` لفحص البيانات، و`--dry-run` لضبط
الشاشة من دون Inquiry. تاريخ مطلوب يعني `--verify` مطلوب، والغموض في الجدول
أو شجرة القسم يجب حله بـ `--grid` أو `--tree`.

```powershell
python run_gmes_workflow.py  # نفس الأسئلة الموجهة ونفس المحرك المسطح
```

الأسئلة الموجهة تفتح الشاشة أولًا ثم تعرض كل فلاترها مرقّمة بأسمائها
وقيمها الحالية، وكل خيارات اللوحة اليسرى بحالتها، والأقسام المتاحة، وشاشات
Quick View بوصفها شاشات أخرى لا خيارات. الاختيار بالرقم أو بالاسم.

## Screen map — Production Plan by Order(Line)

Breadcrumb: `PPM > Production Plan > Prod. Plan Inquiry > Detail Schedule >
Production Plan by Order(Line) [ P1112UM00 > P1112WM00 ]`

| Screen code | Role | Key datasets |
|---|---|---|
| `P1112WF00` | left filter panel | `dsFilterDVO` — `paramFromDate`, `paramEndDate` (YYYYMMDD) |
| `P1112WM00` | results | `dsMasterProdPlan` — 140 columns |
| `OrgCategory_GDS` | Org tab tree | `dsCatCommonTreeNodeDVO` — 34 rows; VD is `^V^C712A^T001` |

Fixed shell ids (safe to hardcode — there is only ever one of each):

```
mainframe.vFrameSet1.vFrameSet2.topFrame.form.btnUserInfo      signed-in user
mainframe.vFrameSet1.vFrameSet2.mdiFrame.form.btnExcel         Excel Download
mainframe.vFrameSet1.loginFrame.form.divLogin.form.btnAdSSO    AD SSO Login
```

## Known gotchas (all already solved by this skill)

1. **Chrome 136+ silently refuses to debug the default profile, and a copied
   profile cannot leave the machine it was copied on.**
   `--remote-debugging-port` is *ignored*, not rejected, whenever
   `--user-data-dir` is the default profile directory — a security fix that
   stopped malware reading cookies out of a live browser via DevTools. The
   port simply never opens, which looks exactly like a launch failure. So a
   separate `--user-data-dir` is mandatory.

   The first answer was to COPY the user's profile
   (`clone_user_profile()` → `%LOCALAPPDATA%\Google\Chrome\CDP Profile`,
   caches excluded, 928 MB → 338 MB). That works, and still does on the
   machine it was made on — but it can never be shipped. **Chrome 140+ wraps
   every cookie on Windows in App-Bound Encryption (the `v20` prefix), whose
   key is derived through Chrome's elevation service and is bound to the
   machine.** A profile copied to a different PC fails to decrypt its own
   cookies with `0x57` and yields nothing, silently. There is no such thing
   as an installer containing a pre-authenticated profile.

   **The supported answer is a profile the tool builds empty**
   (`automation_profile_dir()` → `%LOCALAPPDATA%\GMES_Automation\profiles\…`,
   seeded once by `seed_automation_profile()`, launched by
   `launch_automation_chrome()`). Nothing is copied. The user signs in for
   real once, the session lives in that profile, and every later run reuses
   it — the same benefit the copy gives, without carrying anyone's personal
   cookies, history or extensions. The copy path remains as the explicit
   `--refresh-profile` escape hatch. Neither profile is ever deleted, and the
   real profile is never touched. (HISTORY.md Phase 73.1)

2. **AD SSO is not automatic.** It opens a Samsung ADFS window
   (`stseu.secsso.net`) asking for a Knox ID and password. Credentials come
   from the DPAPI store; they are typed straight into the browser and never
   printed, logged, or passed as arguments.

3. **"Signed in" cannot be detected by the absence of the Login button.**
   Nexacro keeps the login frame in the DOM and merely hides it, so that
   test never becomes true. Check for the user-name button in the top bar
   (`topFrame.form.btnUserInfo`) instead.

4. **The Notice popup appears seconds *after* sign-in completes.** Checking
   once right after the user name appears reports "no popups" and then the
   popup blocks everything behind it. `close_popups_when_they_appear()`
   waits for the first one, closes it, and keeps watching — a real run
   closed two in succession.

5. **Close popups by type, not by name.** The Notice window's id contains
   Korean text (`...portalFrame.공지사항.titlebar.closebutton`). Matching that
   would close exactly one popup on one screen forever. Match the class
   Nexacro gives floating child windows: `.TitleBarControl.titlebarChildFrame`
   → `.closebutton`. Scoped to child frames so the user's work screen is
   never closed.

6. **The Excel export dialog is also a child popup**, so the popup closer
   would close it. Run the closer **only during sign-in**, never around an
   export.

7. **A work screen's ids are renumbered on every open.** The same Inquiry
   button was `winPPM0219_0_516` on one visit and `winPPM0219_0_315` an hour
   later. Nothing inside a work screen may be addressed by full id — match
   the screen code, the CSS class (`btn_LF_Search_New`), or the label.

8. **The breadcrumb is the address.** It ends with the screen codes
   (`[ P1112UM00 > P1112WM00 ]`). Those are stable. The top search box also
   accepts a ScreenID, which is the intended way to reach a screen without
   walking menus.

9. **A Nexacro grid only builds the rows you can see.** Reading the page
   silently truncates a large result — a 5,000-row report becoming the 20
   rows on screen, with the job reporting success. Read the Dataset.

10. **Two traps make the Nexacro app look empty.** Child frames live in
    `_frames`, an `ObjectArray` with numeric indexing — there is no `frames`
    property. And a work screen is **not a frame**: it is loaded into nested
    `Div` components, each carrying its own `.form`. Walking only frames
    finds an empty `WorkMain` shell and nothing else.

11. **A truncating safety cap hides the answer.** The form walk capped at 60;
    the application has 206 forms, so the target screen appeared not to
    exist at all.

12. **Missing Division = zero rows, with no error.** The screen shows
    "Select Search Criteria" and an empty grid. Tick the division in the Org
    tree — `_checked = 1` on the matching row of
    `OrgCategory_GDS.dsCatCommonTreeNodeDVO` — and the UI checkbox follows.
    Find the row by `commonName`, never by row number: the tree is built
    from the user's permissions.

13. **Dates are set through the dataset, not the calendar widget.** Writing
    `paramFromDate` / `paramEndDate` (YYYYMMDD) into `P1112WF00.dsFilterDVO`
    updates the visible date box, because Nexacro binds the two.

14. **An empty result set does not mean the query finished, and a 0 reading
    is never trustworthy evidence that anything changed.** Nexacro clears
    the dataset to 0 the instant Inquiry is pressed, UNCONDITIONALLY -
    before the server has answered at all, whether the real answer will be
    0 or a real number - and refills it once the server actually answers,
    so the count sits at 0 for the whole round trip regardless of outcome.
    Treating any stable zero as settled once reported 0 rows and refused to
    export while 790 rows were on their way (closed: zero settles too, with
    a long grace period, `InquirySettle`'s `unconfirmed_settle_checks`).
    A second, subtler trap remained even after that fix: a STALE nonzero
    count already sitting in the dataset (from an earlier successful query
    on the same screen) dropping to 0 on click looks, for one poll, exactly
    like "the count changed" - and once `InquirySettle` believed a real
    change had been seen, it trusted a SHORT run of stable zeros as
    "confirmed" fast, before the real (nonzero) answer had time to arrive.
    Live-caught on Q2111UM00: the exact same division/date that had
    returned 175 rows hours earlier reported "0 rows, confirmed" in ~4
    polls, and a manual re-click of the identical query moments later
    returned 175 correctly. A 0 reading must never, on its own, count as
    evidence the query re-ran - only a NONZERO reading that differs from
    the pre-click baseline does; a stable zero always pays the same longer
    patience an unconfirmed-but-unmoving nonzero count already pays,
    stale baseline or not. (HISTORY.md Phase 71, 82.16)

15. **The Excel toolbar icon opens a dialog, it does not download.**
    `PopupExcelExport` — "Save to Excel", grid already ticked, "save a
    single file" ticked. The file arrives only after **OK**.

16. **Chrome's download redirect dies with the connection that set it.**
    `Browser.setDownloadBehavior` must be issued on the *same* open CDP
    connection that does the clicking; setting it from a connection that is
    then closed leaves the file in the user's Downloads folder. Watch that
    folder as a fallback anyway.

17. **The exported workbook is DRM-encrypted.** It begins
    `<## NASCA DRM FILE - VER1.00 ##>` (Samsung NASCA document rights
    management). It opens normally in Excel on a machine running the DRM
    client, but **no library can parse it** — `openpyxl` and `pandas` see a
    corrupt file. If anything downstream must read the data, write a CSV
    from the dataset alongside it.

18. **The dataset carries filler rows the grid hides.** 85 rows with no
    `poNo` and no `masterLine`. 875 − 85 = 790, matching the screen's own
    total exactly — and explaining an earlier unexplained 701-vs-784 gap.
    Drop rows with an empty `poNo` so the count agrees with what the user
    sees. GMES's own Excel export is unaffected, since it exports the grid.

19. **Verify the result date before exporting.** A stale result set looks
    exactly like a fresh one, and an export of the wrong day is worse than
    no export. `verify_result_date()` refuses to continue on a mismatch.

20. **`input()` may have no keyboard.** Launched from a runner without an
    interactive stdin, the credential prompt died with `EOFError`. Fall back
    to a Tk dialog when `stdin.isatty()` is false.

21. **`menuId` is G-MES's T-code, and the whole directory is client-side.**
    `gdsMenuList` (1177 rows) holds every screen the account can reach — 809
    of them — with `menuId` (PPM0219), `sysScreenId` (P1112UM00),
    `enMsgCont` / `koMsgCont`, and `screenSn`, the ancestor chain that
    reproduces the on-screen breadcrumb exactly. `menuId` is the identity
    that matters: it is what `gdsOpenMenu` records, and what the window is
    named after (`winPPM0219_0_603`).
    **Do not use `gdsMenuList_`.** It looks like the same catalogue but its
    titles are Korean only and its menuIds are a different series (`PM0001`
    vs `PPM0219`), so searching or joining it in English silently returns
    nothing.

22. **The search box must be typed into with real key events.** Setting the
    value through Nexacro's own `set_value()` fills the box and searches
    nothing — the suggestion list is produced by the control's `onkeyup`
    handler. Click the box to focus it, then send per-character
    keyDown/char/keyUp.

23. **The Notice popup is MODAL and swallows every click silently.** While
    it is open the application is greyed out; the search button appeared
    completely dead until the popup was closed first. Clear popups before
    interacting with anything.

24. **Click the result GRID row, not the detail panel.** Suggestions live in
    `integratedSearch.form.grdResult`, bound to `dsSearchResult`; the row to
    click is `grdResult.body.gridrow_<n>.cell_<n>_0`. The popup also shows a
    detail panel (`divDetail.form.staTitle`) carrying the same text — it is
    the obvious visual match, and clicking it opens nothing at all. Read
    `dsSearchResult` to choose the row by data, then click that row.

25. **Open is not the same as active.** G-MES keeps every opened screen
    alive behind tabs. A background screen still accepts dataset writes, so
    filters can be applied to one screen while the Inquiry click lands on
    whichever screen is actually in front. That produced a run which set the
    date and division correctly, queried a completely different report, and
    reported zero rows. Activate the tab (`mdiFrame.form.divTab.form.TAB_<winId>`)
    and confirm the window became visible before acting.

26. **Dataset dumps can contain live session tokens.** The integrated-search
    form's `dsAnyframeDVO` carries `tokenId` and `refreshTokenId` — full
    JWTs for the signed-in session. Never paste raw dataset output into
    documentation, commit messages, issues or chat. Print only the columns
    needed.

27. **Clicking AD SSO does not always open an SSO window.** When a session
    cookie has survived in the profile copy, G-MES signs straight back in
    and no Samsung ADFS page is ever shown. Waiting only for that window
    failed a run with "the Samsung SSO window never opened" while the user
    was already signed in. Wait for **either** the SSO window **or** a
    completed sign-in, whichever arrives first.

28. **The "extra" rows are SUBTOTALS — LINE SUM and PROC SUM.** The result
    dataset carries rows with an empty `poNo`, `masterLine` and `modelCode`
    but the same `planQty` / `acrsQty` as the data row above them. On screen
    the grid renders those rows labelled **LINE SUM** and **PROC SUM**: the
    labels are added by the grid at render time and are *not* stored in the
    dataset, which is why they look blank when read from the data layer.
    That explains the arithmetic exactly — 875 − 85 = 790, the grid's own
    total — and it means dropping rows with an empty `poNo` is correct for a
    data extract, because it drops subtotals rather than data.
    *(Previously recorded here as an unexplained inference; confirmed by
    inspecting a single-PO result, where 1 data row came with 3 subtotal
    rows carrying identical quantities.)*

29. **Filters can be discovered, not hand-taught — `form.binds` is the map.**
    Every Nexacro form carries a binding table linking each control to the
    dataset column behind it:
    `divBasic.form.divCal.form.mskDateFrom → dsFilterDVO.paramFromDate`.
    Read it and a screen describes its own filters, with the label rendered
    beside each control. The result grid is found the same way, via its
    `binddataset`. Three caveats learned immediately:
    - Bound **Statics** are computed outputs (`staPlanQty`, `staProgRate`),
      not filters. Classify by the control-name prefix: `edt/msk/cbo/chk/
      rdo/cal/spn` are inputs, `sta/img/btn` are displays.
    - The shell's own forms (`WorkMainTitle`, `MyMenu`, `LeftMain`) carry
      binds that belong to the frame, not the report. Exclude them.
    - **Not every screen binds its filters.** `Q2241UM00` binds none and
      sets them in code, so a bind-only tool reports "no filters" and looks
      broken. List the visible-but-unbound inputs too, and say plainly that
      they cannot be set through a dataset.

30. **Never poll a result dataset by a hardcoded name when the screen can
    vary.** Running *Production Plan by Model* with the Production Plan
    job's inquiry helper reported **875 rows** — the count still sitting in
    the previous screen's `dsMasterProdPlan` — when that screen had actually
    returned **17**. The export was correct, because it used the discovered
    dataset; only the number and the settle-wait were wrong, which is the
    more dangerous combination: the file is right, the log lies, and nobody
    checks. Poll the dataset discovered on the screen being run, and require
    the count to be seen **changing** so a dataset left populated by a
    previous run is not mistaken for a finished query.

31. **Run multiple reports sequentially, never in parallel.** One tab is in
    front at a time and a background screen still accepts filter writes;
    the Excel button and its dialog are global to the application; modal
    popups block everything. Parallelism would need separate Chrome
    instances, profile copies and SSO sign-ins for a gain measured against
    ~12-30s of server time per report. Isolate each screen instead, so one
    failure does not stop the rest.

32. **The left panel carries filter dimensions that are not "filters".**
    Each of these changes what a query returns, and each is a labelled
    button or checkbox — not a bound field, so bind-discovery alone misses
    every one of them:

    | Option | Effect |
    |---|---|
    | `Org` / `Prod` / `Fac` / `Proc` | which category tree the selection comes from |
    | `STD` / `PLANT` | the organisation attribute |
    | `Including Past Org.` | include closed organisations |
    | `Plan Date` / `Create Date` | **which date the period means** |
    | `General` / `Compare` / `OI` | the search mode |
    | Quick View entries | e.g. Master vs Detail Prod. Plan — a different result set |

    Nexacro encodes the state in the CSS class: `_Sel`, `Category_Sel` or
    `ToggleSearchV2` mean chosen; `_Dis` or `_Default` mean not. So they can
    be listed with their current state and set by label —
    `--option PLANT`, `--option "Create Date"`. Setting one is verified by
    re-reading the class, never assumed. Apply them **before** the Division
    and the filters: switching a category tab or Quick View rebuilds the
    panel and discards what was set.

33. **A generic CSV must not guess which column is the key.** The Production
    Plan job drops rows with no `poNo`; on an unknown screen there is no
    equivalent to drop on. Filtering one PO returned **four** dataset rows
    for a single visible line — three continuation rows the grid merges.
    The generic exporter therefore drops only completely empty rows and
    reports both counts. Silently discarding rows on a screen whose shape
    is unknown would be worse than a larger file.

34. **Filters PERSIST on an open screen, and are inherited silently.** G-MES
    keeps a screen alive behind its tab, so a value typed into a filter
    stays there for every later run. A run asking only for a date returned
    **zero rows** because a Production Order from the previous run was still
    in the box — the date was right, the division was right, and the answer
    was empty with nothing to indicate why.
    Before applying a run's own filters, blank the free-text (`edt`) fields
    the caller did not name. Do **not** blanket-clear: combos and checkboxes
    hold meaningful defaults (`paramTecoYn` = "All", a status list =
    "1^2^3^4") and emptying those breaks the query a different way.

35. **Accept what people actually type.** Two real failures in one session:
    `vd` did not match the Org tree's `VD` because the comparison was
    case-sensitive — with the answer sitting in the error message it
    printed — and `2026-09-07` was written verbatim into a filter that
    stores `YYYYMMDD`, so the query silently answered something else.
    Match organisation and screen names case-insensitively, normalise dates
    to `YYYYMMDD`, and **reject** anything that is not a real calendar date
    rather than passing it through.

36. **Address the DevTools endpoint by `127.0.0.1`, never `localhost`.** On
    Windows, `localhost` resolves to `::1` first and Chrome listens on IPv4
    only, so every call waits for the IPv6 attempt to fail. Measured: **2.05s
    per `/json/list` via `localhost` against 0.013s via `127.0.0.1`** — a
    150x difference. It is paid by every target lookup *and* every websocket
    connect, because Chrome hands back `webSocketDebuggerUrl` values pointing
    at `localhost`, so those must be rewritten too (`cdp_common.ipv4()`).
    Fixing this took the startup path from 12.5s to 0.41s.

37. **Only wait for the Notice popup after an actual sign-in.** It arrives a
    few seconds *after* the user name appears, so a fresh sign-in has to
    watch for it (gotcha #4). But that 45-second vigil ran on every
    invocation, including sessions that were already signed in, where any
    popup would already be on screen. Every command paid it before doing any
    work: `gmes_login.py` took **58.9 seconds** on an already-signed-in
    session and closed nothing. Wait when signing in; sweep once otherwise.

38. **A control with no binding is still driveable — with real keys.**
    `form.binds` finds the fields a screen declares; a screen that sets its
    filters in code (`Q2241UM00`) declares none. Those controls cannot be
    written through a dataset, but they can be typed into, because Nexacro
    reacts to key events and not to a value assignment — the same property
    that makes the top search box searchable (#22). Click, Ctrl+A, Delete,
    per-character keyDown/char/keyUp, Tab to commit, then **read the control
    back**: an unfocused Nexacro edit is a `div` carrying text, a focused one
    renders a real `<input>`, so the read has to handle both.

39. **Date fields are not all called `fromDate`, and are not all 8 digits.**
    Screens store `planYmd`, `stdYm`, `paramPeriod1`. Recognise a date by
    whole words in the column or label (`date`, `ymd`, `ym`, `dt`, `period`,
    `day`) or by the control type — `msk` and `cal` are Nexacro's masked
    edit and calendar. Match **words**, never substrings: `paramVendorCode`
    contains "end". And write the width the field already holds — a `YYYYMM`
    field given eight digits is accepted, and the query then answers a
    different question.

40. **Every left-panel category tab has a tree of its own.** Org / Prod /
    Fac / Proc are not one tree with four filters. Find them by shape — a
    dataset with a `commonName` column and a `_checked` flag — and choose by
    data: the tree that actually contains the name asked for. `OrgCategory_GDS
    .dsCatCommonTreeNodeDVO` is the Org one, not the only one.

41. **A tick in a category tree persists exactly as a typed filter does.**
    The screen stays alive behind its tab, so a division ticked by one run is
    still ticked for the next, and a run asking for a different one queries
    both. Clear the ticks the run did not ask for, and say which were
    cleared.

42. **More than one grid can be bound on the same screen.** Master-detail
    screens have two, and "the biggest visible grid" is then a coin toss with
    no error either way. Report the ambiguity and let the caller name the
    grid.

43. **G-MES fills its own `localStorage` until the app cannot start.** The
    Nexacro engine (~99 KB) is cached under a key of
    `<epoch-ms><engine-url>`, and a new one is written on load while the old
    ones are never removed. At about fifty loads the 5 MB per-site quota is
    full, the bootstrap throws `QuotaExceededError: setItem`, and
    `nexacro.getApplication()` stays empty - a blank page with a spinner,
    `readyState` "complete", three divs, **no failed requests and no HTTP
    errors**. Reloading cannot help; the storage is still full. Observed at
    102 entries / 5.00 MB / 52 engine copies; removing 51 freed 4.94 MB and
    the app built in under four seconds, session intact.
    Automation hits this far sooner than a person. Use
    `gmes_common.prune_nexacro_cache()`, and tell a slow page from a dead one
    with `gmes_common.app_is_built()` - both look blank from outside.

44. **The user's own Chrome being open is not a conflict.** The automation
    runs on a *copy* of the profile (`CDP Profile`), and Chrome will start a
    second instance on a different `--user-data-dir` quite happily -
    measured with 30 of the user's chrome.exe processes running. The rule
    "Chrome will not hand over a profile already in use" applies to the same
    profile directory, which is what the copy exists to avoid. Do not make
    anyone close their browser to run a report.
45. **A screen can hold the same category tree SEVERAL times, and writing one
    copy is not enough.** Work Calendar has **three** instances of
    `OrgCategory_GDS.dsCatCommonTreeNodeDVO`, one per panel tab.
    `_dataset()` returns whichever the form walk reaches first, so a
    `_checked` write can land in a copy that is not the visible tree — a run
    reported "division VD" while the screen still showed MOBILE ticked by
    hand, and 288 rows of MOBILE data were exported in a file labelled VD,
    with no error anywhere. Write **every** instance, and then verify against
    the screen's own summary label (`staCategory` / `staCategoryOri`, reading
    `"Org VD l Prod All l Proc All"` — the separator renders as a lowercase L
    in one place and a pipe in another). That label follows a dataset write
    immediately, so it is a sound check. A dataset write returning without
    error proves nothing.

46. **"Quick View" is a shortcut to a DIFFERENT SCREEN, not a filter, and it
    renders as a Grid so the left-panel option scan never sees it.** On
    `P1114WM00` (PO Batch Monitoring) a "Quick View" panel lists **PO Batch
    Monitoring** and **PO I/F Monitoring**; reading its dataset (`dsWidget`,
    on the shared `WidgetFilter.xfdl.js`/`WidgetMain.xfdl.js` shell form
    present on every screen) shows it is a filtered slice of the same menu
    catalogue behind the top search box (#21) — `menuId`/`sysScreenId` pairs
    (`PPM0693`→`P1114WM00`, `PPM0694`→`P1114WM01`), not query options.
    `JS_LEFT_OPTIONS` only recognises `Button`/`CheckBox`-classed elements;
    this is a `Grid` (`grd_LF_QuickView`), so every element in it — grid,
    rows, cells — was silently skipped, and RECORD never mentioned the panel
    existed at all, even though clicking a row changes which screen is being
    driven. `discover()` now finds it by shape (a dataset carrying
    `sysScreenId` + `menuId` + `quickViewId`) and reports it; the RECORD
    display shows the active entry and names the siblings as separate
    screens to be opened by their own UI number, and does not click them.
    **A related leak, also fixed.** The same shell forms
    (`WidgetFilter.xfdl.js`, and `OrgCategory_GDS.xfdl.js` for
    `grdOrgCategory`) are not covered by the `SHELL` exclusion regex used for
    the *grids* walk, so `grdWidgetList`/`dsWidget` and the org tree's own
    grid were both leaking into the result-grid candidate list. Adding their
    file names to `SHELL` was not the fix, because `OrgCategory_GDS.xfdl.js`
    is the form the org tree is legitimately discovered on (#40) — excluding
    it by filename would have hidden the Division tree itself, not just its
    grid. Fixed by dataset **shape** instead: any dataset shaped like an org
    tree (`commonName` + `_checked`) or the Quick View widget (`sysScreenId`
    + `menuId` + `quickViewId`) is collected by name once per screen, and a
    grid bound to one of those names is excluded from the *result* candidates
    only — `JS_ORG_TREES`'s own tree discovery is a separate walk and is
    untouched. One more trap found while fixing it: a grid's own form does
    not always carry the dataset it renders as an *own* property — Nexacro
    resolves `binddataset` through the form's ancestor scope at render time,
    so looking the dataset up only on the grid's own form (`h.form[bd]`)
    silently found nothing for `grdWidgetList` and let it straight through.
    The fix collects chrome dataset names from every form in the window
    first, then filters grids by name against that set.

47. **A `…WM00` work-form never gets its own `gdsOpenMenu` row — only its
    `…UM00` shell does — so opening it while the shell is already open times
    out.** `P1114WM00` (PO Batch Monitoring's grid form) has its own
    catalogue entry (`menuId` `PPM0693`, same as gotcha #46), so it is a
    valid argument to the search box and to `gmes run`/`gmes_report.py run`.
    Live: with `P1114UM00` (the shell, `PPM0221`) already open as a tab,
    `gmes run P1114WM00` clicked the correct catalogue result but then waited
    the full 90s and failed with "P1114WM00 (PPM0693) did not open within
    90s. It may not be permitted for this account." — a message that reads
    like an authorization problem but is not one. **Cause**: `P1114WM00.xfdl.js`
    is loaded *nested inside* `P1114UM00`'s tab (see its own form path,
    e.g. `...workFrameSet.winPPM0221_2_373.divWorkMain...`), and `gdsOpenMenu`
    only ever records the shell's `winPPM0221_...`/`PPM0221` row — a `PPM0693`
    row is never going to appear there. Clicking the search result for an
    already-embedded work-form re-hits the already-open shell tab, so no
    *new* tab appears either, which is the only other condition the open-wait
    loop accepts. A cold start (nothing open yet) does not hit this: clicking
    then opens a genuinely new shell tab, which the "any new tab" fallback
    catches. **Fix** (standalone `gmes`, `discovery/catalogue.py`'s
    `tab_for_embedded_form()`): before falling through to a catalogue search,
    check whether the requested code is already loaded as a nested form
    (`query.form_locator.list_forms`); if so, resolve its containing tab from
    the win-id embedded in the form's own path and activate that tab directly
    - the work-form's dataset is then reached exactly as `gmes data read`
    already does. This only helps once the shell has been opened at least
    once in the session; a screen that has genuinely never been opened still
    works via the existing cold-start path. **Also ported to the legacy
    `gmes_open_screen.py`/`gmes_core.py`** (`tab_for_embedded_form()`,
    called from `gmes_core.open_screen()`) at explicit user request, as a
    deliberate, one-off exception to the migration plan's freeze on legacy
    scripts (HISTORY.md Phase 41.4) - the identical failure was reproduced
    live through `run_gmes_workflow.py` before the port.

48. **Clicking a popup's X is not the same as the popup closing, and there
    is a second way through.** A close button that is visible and correctly
    targeted can still swallow the click; nothing errors, and the notice
    then sits on top of whatever is clicked next, so the run fails on an
    unrelated-looking control. The fallback is calling the frame's own
    `_on_closebutton_click()` - **not** `ChildFrame.close()`: that method was
    the original guess here and does not exist on this Nexacro version at
    all (confirmed live, HISTORY.md Phase 59.1 - `_closePopup()` also exists
    and runs without error, but was confirmed live NOT to remove the popup;
    only `_on_closebutton_click()` actually did). The frame is addressed
    from the title bar's DOM id with its trailing `.titlebar` removed -
    Nexacro builds that id from the component path, so it is the popup's
    real address. Resolving it needs BOTH a property walk and a `_frames`
    search by name (gotcha #10): the notice frame is named `공지사항` and is
    not a property of its parent. Always confirm the popup is gone rather
    than counting the click or the call not throwing - a call "succeeding"
    proves nothing here, only a re-check of the DOM does. Implemented in
    `gmes_common.fallback_close_popup()`, wired into
    `close_popups_when_they_appear()` and `close_child_popups()`.

49. **Gotcha #6 means "never close what you cannot identify", not "never
    close anything during a run".** The Excel export dialog is a floating
    child popup, so a blanket closer would cancel an export - but a notice
    raised *while a screen is open* swallows the Inquiry click just as
    surely, and waiting until the next sign-in is not a fix. Close only
    popups whose own title bar identifies them (공지사항 / Notice /
    S9502UP…), refuse anything else, and sweep only at points where nothing
    of ours is in flight: screen open, before Inquiry, before the Excel
    icon. Never between the Excel icon and the downloaded file.

50. **The export dialog's confirm button is not always labelled "OK".**
    Matching one exact label makes an unattended job fail on a button that
    is on screen under a different word. Try the known set (OK / 확인 / Ok /
    Yes / 예 / Save / 저장), and when none is found, say which ones were
    looked for rather than reporting a missing dialog.

51. **A machine's own Chrome policy can silently block the AD SSO popup.**
    "AD SSO Login" opens the ADFS page with `window.open()`. This machine's
    `PopupsAllowedForUrls` GPO whitelists many other Samsung sites but not
    `seegmes4.sec.samsung.net` or the SSO host, so Chrome's default popup
    blocker swallowed the window with nothing for `Runtime.evaluate` to see
    - not a slow SSO window, one that never existed. `find_sso_window()`
    then correctly found nothing for the full 45s, and the code fell
    through to the stored-password form login, burning a real attempt
    against the account's login-lockout counter (HISTORY.md Phase 56.1).
    `launch_chrome_with_user_profile()` now passes `--disable-popup-blocking`
    - check this before assuming a credential is wrong.

52. **A work-screen tab can have no close ("X") element in the DOM at all.**
    Confirmed live on a real "Production Plan by Order(Line)" tab: its tab
    bar element's only child was a text label - nothing with "close" in its
    class or id existed to find, so `JS_TAB_CLOSE_TARGET` correctly reported
    no control, every single time, for this tab shape. The batch runner does
    not treat a failed close as fatal, so this was silently leaving tabs
    open on every run - very likely the real cause behind gotcha-shaped
    findings like "reusing one tab inflates discovery" (HISTORY.md Phase
    58.2), discovered before this cause was known. The real, live-confirmed
    fix is the application's own close handler, not a DOM search:
    `nexacro.getApplication().gvMdiFrame.form.fnRemoveForm(winId)`, needing
    only the win_id already in hand. `Screen.close()` tries the DOM control
    first (some tab shapes may still have one) and falls back to this call,
    verifying either way via `open_screens()` rather than trusting the click
    or the call not throwing (HISTORY.md Phase 60.1).

53. **The CDP port is not a number this project chooses any more — it is a
    property of the profile.** Chrome is launched with
    `--remote-debugging-port=0`, the OS hands back an already-bound port, and
    Chrome writes it into `DevToolsActivePort` inside that instance's own
    `--user-data-dir`. Picking from a port *range* instead looks equivalent
    and is not: choosing a free port and then handing it to a subprocess to
    bind leaves a race that Selenium still files bugs about.
    **The file outlives Chrome**, so it names a dead port after the browser
    exits — never treat its presence as success. Every read is paired with a
    live check (`cdp_is_up()`), and the launcher deletes a stale one before
    starting, or the wait loop reads the old port and times out while the new
    browser answers happily somewhere else. `cdp_common.active_port()`
    resolves in order: already known in this process → recorded in the
    profile by whichever process started the browser → explicit
    `NERP_CDP_PORT` → the historical 9444. The middle step is what lets
    `gmes_data.py` in a second terminal find the browser `gmes_report.py`
    started. (HISTORY.md Phase 73.2)

54. **A screen's structure can be shared; what a run did with it cannot.**
    `screens/<CODE>.json` mixes the two — which control is the from-date and
    which grid holds the result (true for anyone, and expensive to derive)
    alongside the division, dates, filter values and command of the run that
    proved it (production data, CLAUDE.md 2.4). `gmes_profile.shippable()`
    splits them: `screens_known/<CODE>.json` is committed and ships,
    `screens/<CODE>.json` stays local and git-ignored, and `load()` lays the
    local half over the shipped one. It is an **allowlist**, so a field added
    to `save()` later cannot leak by being forgotten. Note `division` ships as
    `{form, dataset}` only — where the tree lives is the screen's, which
    division was ticked is the user's. (HISTORY.md Phase 73.3)

55. **A failed AD SSO is NOT a failed password, and treating it as one costs
    an account lockout attempt.** G-MES refuses a bad form login with a
    modal that COUNTS: *"5회 불일치할 경우 로그인이 제한됩니다.(시도횟수1/5)"* -
    five mismatches and the account is restricted. The old code answered any
    AD SSO failure by typing the saved password into G-MES's own login form,
    reasoning that "the credentials are already in hand". Live-proven wrong
    (Phase 74.1): the SSO window had not opened for a reason unrelated to the
    password, the form submission failed for that same reason, and a correct
    password was counted as attempt 1 of 5.
    **The password path is now opt-in** (`--allow-password-login`, off by
    default; every automated entrance inherits the safe default).
    `lockout_warning()` reads the counting modal and is the ONLY signal that
    proves a refusal - any run that sees it stops and must not retry. The
    login form's own error span is **not** such a signal: it has been observed
    carrying "check your ID or password" with nothing submitted at all, after
    a click on the language toggle alone. `wait_for_sso_window()` returns
    `"no-window"`, not `"rejected"`, because the two are different facts.

56. **The UI language is not controllable from the Chrome profile, and one
    mechanism depends on it.** A profile the tool builds renders G-MES in
    Korean; the old copied profile renders it in English. Ruled out live as
    causes: `intl.accept_languages` (matched exactly, `navigator.languages`
    confirmed, still Korean across two restarts), cookies (only `JSESSIONID`
    and a per-load random `_xm_webid_1_` - no locale cookie), and
    `localStorage` (no language key). Whatever chooses it is out of reach.
    Impact was exactly one mechanism, **closed in Phase 76**: `set_option()`
    now matches by the control's own Nexacro `name`, with the rendered label
    kept only as display metadata (#58). Everything else already matched on
    something language-independent - ids, CSS classes, dataset columns,
    `commonName`, and the catalogue's `enMsgCont`/`koMsgCont` fallback (#21).
    **Do not add any new mechanism that matches on visible text.**

    **Correction, Phase 77: the login page's "English" toggle DOES translate
    the page, immediately, when clicked with a real dispatched mouse event -
    this entry previously said otherwise and was wrong.** A plain `.click()`
    would explain the earlier, incorrect finding: Nexacro's controls ignore it
    everywhere else in this project, and this is presumably no exception. See
    #60 for the live-verified mechanism and the fix that now uses it.

57. **Chrome and Edge are the same browser for this purpose, and the profile
    the employee already uses is the one worth having.** Both are Chromium, so
    every flag, every CDP call and every profile mechanic in this document
    applies unchanged to Edge - which matters because on a corporate Windows
    image Edge is frequently the default, and the G-MES session the employee
    already has is in whichever one they actually use. `gmes_browsers.py`
    resolves it from the Windows https association
    (`...\Shell\Associations\UrlAssociations\https\UserChoice` → `ProgId`;
    `ChromeHTML` / `MSEdgeHTM`, both carrying a per-install suffix, so match
    the prefix), falls back to the other supported browser, and falls back
    again to an empty tool-built profile.

    Four things about the copy that are not guessable:

    - **`Local State` at the user-data-dir ROOT is not optional.** It holds
      `os_crypt.encrypted_key`, the DPAPI-wrapped key the cookies are
      encrypted with. Copy only the profile folder and every cookie in it is
      undecryptable - silently, yielding a signed-out session rather than an
      error.
    - **The profile must be renamed to `Default` AND the index rewritten to
      agree.** The source may have called it `Profile 2`; `Local State`'s
      `profile.info_cache` / `last_used` still name that. Left disagreeing,
      the browser shows a profile picker instead of the page - *with*
      `--profile-directory=Default` on the command line, so it looks like
      anything but a profile problem.
    - **Cookies live in `<profile>\Network\Cookies` on current builds** and in
      `<profile>\Cookies` on older ones. A corporate image can be running
      either, so "does this profile have a session" has to check both.
    - **`robocopy` reports success for everything it managed.** A browser
      holding its cookie database open produces a clean-looking copy with no
      session in it, so the copy is verified by what arrived, not by the exit
      code. Exit codes 0-7 are its ordinary success bitmask; 8+ means a file
      could not be read, which almost always means the browser is still open.

    The copy happens **once** - a second one would overwrite the G-MES session
    the automation has since earned, which is the Phase 20 loss - and never
    crosses machines, because of #1's App-Bound Encryption. (HISTORY.md Phase
    75)

58. **A left-panel option's identity is its Nexacro `name`, never its label -
    and the UI language is the account's, not the browser's.** Every control
    in the left panel carries its own component name, authored in the screen's
    XFDL and identical in every language, while the text it renders sits in a
    separate `_displaytext` property:

    | rendered | `name` | rendered | `name` |
    |---|---|---|---|
    | 생성일 | `btnCreate` | 과거 조직도 포함 | `chkDisuseYn` |
    | 계획일 | `btnPlan` | DB 조회 | `chkPoSearch` |
    | 실적일 | `btnProduce` | 일반 검색 | `btnSearchNormal` |
    | 조회 | `btnSearch` | 비교 검색 | `btnSearchCompare` |
    | STD / PLANT | `btnstd` / `btnplant` | Org / Prod / Fac / Proc | `tabTitle_Org` / … |

    Store the name and the semantic key derived from it (`btnCreate` ->
    `create`; strip `tabtitle_` before `tab`, or `tabTitle_Org` becomes
    `title_org`). A profile that stored the label stopped replaying the moment
    the same screen rendered Korean - `"Create Date"` could not be found
    though the control was right there (HISTORY.md Phase 76).

    **The language is not yours to set.** `navigator.language` is already
    `en-US` while the application holds its own `gvLanguage = 'ko'` - an
    account-side preference, which is why Phase 74.3 ruled out
    `intl.accept_languages`, cookies and `localStorage`, and why the login
    page's "English" toggle flips itself and translates nothing. Changing it
    would be a write to the user's G-MES settings (CLAUDE.md 2.5). Do not
    depend on it; identify controls by something that is not rendered.

    **Rescuing an old English label needs a NARROW rule.** Only "the whole
    request is the key" (`PLANT` -> `plant`) and "the first word is the key"
    (`Create Date` -> `create`). A "key appears anywhere in the request" rule
    matched *org* inside `Including Past Org.` and picked the Org **category
    tab** instead of the checkbox it means; a prefix rule matched `PLANT`
    against both `btnplant` and `btnPlan`. Refusing with diagnostics beats
    either, because a wrong match returns a different report with no error.

59. **A successful AD SSO popup turns into a second G-MES application, and
    nothing used to close it.** `window.open()` opens ADFS (#51); on success
    that window follows its own RelayState back to the G-MES host, so it stops
    being an SSO window and becomes a complete Nexacro app with no work
    screens. Observed live at **four** G-MES browser tabs, three of them
    empty. It matters because `gmes_tab()` takes whichever page the browser
    lists first, so a run can attach to an empty duplicate while the screens it
    opened sit in another tab - the same failure shape as #25. Keep exactly one
    (`gmes_common.prune_duplicate_gmes_tabs()`), keep the one that HAS work
    screens open, never touch an SSO tab mid-flight, and confirm by re-listing
    rather than trusting that the close was accepted (#48). Closing a tab is
    not closing a browser (CLAUDE.md 2.6). (HISTORY.md Phase 76.4)

60. **The login page's language toggle works with a real click, and is a
    different mechanism from the account's `gvLanguage` entirely.** The two
    controls (`...loginFrame.form.divLogin.form.staEng` /
    `...staKor`) share one base CSS class; Nexacro moves a `V2` suffix between
    whichever one is NOT currently selected, rather than fixing it per
    element - confirmed live by toggling both directions and reading the
    class each time. **Read BOTH controls, not one**: a bare check of `staEng`
    alone cannot tell "English is selected" apart from "neither carries the
    suffix right now"; require the two to disagree, and treat agreement (both
    selected, or neither) as an unrecognized state to leave alone rather than
    guess through (CLAUDE.md 3.9). Clicking the one without the suffix via
    real mouse dispatch (`click_element_by_rect`, never `.click()` - see the
    corrected #56) re-renders every visible label on the login form
    immediately, fires **zero network requests** (captured with
    `Network.enable` over a 3s window), and does not survive a `Page.reload()`
    or a fresh navigation - so it must be applied on every fresh login form,
    not once. `gmes_login.ensure_login_language_english()` does this on every
    `state == "login"`. It never touches `gvLanguage` (#56/#59's mechanism,
    still account-side and still untouched) and nothing on this page is
    addressed by rendered text to begin with, so switching it changes nothing
    about which control gets clicked. (HISTORY.md Phase 77)

    **Making English the default rendering promotes an unverified guess to the
    primary path**, and this is the trap to watch for on any future screen:
    `lockout_warning()`'s English markers (gotcha #55/#57's mechanism) had
    never been observed live - deliberately, since observing them means
    spending a real lockout attempt (#55) - and before this phase a real
    refusal almost certainly rendered in the already-verified Korean wording.
    After it, English is the default, so an unverified guess now sits in the
    primary path rather than a backup. Closed the only way that does not need
    the untested wording: a language-INDEPENDENT structural marker, the
    counter's own `(N/M)` shape in parentheses, alongside the guessed phrases
    rather than instead of them. Whenever a fix changes which language
    something renders in, check whether anything downstream was relying on
    guessed - not observed - wording in that language, and prefer a structural
    signal over a better guess. (HISTORY.md Phase 77.3)

61. **The LOGIN page has its own modal popup family, separate from every
    post-signin one.** `close_child_popups()`/`find_child_popups()` sweep
    `mdiFrame` popups (Notice windows, gotcha #5); a session-conflict warning
    - "Currently being used by another PC or terminated abnormally.",
    `mainframe.vFrameSet1.loginFrame.UserIpCheck` - lives under `loginFrame`
    instead and is never found by that scan. It means this account's OWN
    prior session went stale, not that a different person is using the
    account or that a credential is wrong. G-MES is fully modal while it is
    open (gotcha #23's rule, one screen earlier): `wait_for_login_or_session()`
    can only confirm the AD SSO button EXISTS in the DOM, not that nothing is
    covering it, so a click on it lands on nothing and the automation reports
    "the SSO window never opened" - a real, but misleading, symptom of a
    cause it has no way to see. `gmes_login.close_login_ip_check()` clicks
    this popup's own OK/confirm button (never its titlebar X - this is a
    confirmation to proceed, not a dismissible notice) at the very start of
    every `state == "login"` pass, before the language toggle or the SSO
    click, since both sit behind it if it is open. (HISTORY.md Phase 82.14)

    **This popup can also interrupt a session that was already working**,
    not only one being established - G-MES's session guard can kick an
    already signed-in account at any moment. `gmes_login.main()`'s own
    check only fires while a sign-in is actively being attempted, so a run
    already past sign-in had nothing watching for a LATER kick until the
    next full sign-in happened to run - live-caught mid-batch, the popup
    appeared while the tool was simply idling between two screens.
    `gmes_core.recover_from_session_kick()` closes that gap: the same
    cheap check, run unconditionally at the start of `open_screen()` - the
    one choke point every screen-open in the project passes through -
    rather than only inside sign-in itself. Dismissing the popup alone
    does not restore the session (confirmed live: `is_logged_in()` read
    `False` immediately after), so finding it triggers a full `sign_in()`,
    not just a click. (HISTORY.md Phase 82.15)

    **The cause, finally: G-MES tabs accumulating across launches.** G-MES
    allows one session per account and `UserIpCheck` is literally that
    check - each open G-MES page is a Nexacro application handshaking the
    same account, and the server invalidates one. The launcher used to pass
    `--restore-last-session=false`. **Chrome switches are presence-based -
    the `=false` is ignored - so that flag REQUESTED a restore**: every
    launch reopened every G-MES tab from every earlier run. Measured live
    on a cleanly-closed profile: 2, 3, 4, 5 pages over successive
    relaunches with the flag; exactly 1, stable, without it. Never add any
    spelling of `--restore-last-session` back (a test forbids it). Note the
    trap in diagnosing this: `Preferences` reads `exit_type: Crashed` for
    as long as the browser is RUNNING, so it is not evidence of a crash -
    an earlier fix built on that reading did nothing and was removed.
    Detection and recovery above are still worth having; this is what stops
    it happening. (HISTORY.md Phase 82.17)

62. **A grid's `binddataset` is a live property - the screen can re-bind it
    once the query answers.** R5216UM00's `grdDetail` is bound to
    `dsMntDetailListTemp` (one column, never a row) when the screen is read,
    and to `dsMntDetailList` (the real rows) afterwards. Discovery describes
    the screen BEFORE that, so a run polled a dataset that could not fill and
    reported "no rows" beside a screenshot of 259. Symptom to recognise: 0
    rows, from a dataset with a placeholder-ish name (`...Temp`, one
    column), on a screen that visibly has data. `Screen.follow_grid_rebind()`
    re-looks-up the same component (name + form path) after EVERY Inquiry
    (`reconcile_result()`, HISTORY.md Phase 82.20) and follows the new binding. A profile records `grid_aliases` so the fingerprint and
    the grid lookup treat both names as one screen - without that, a profile
    recorded from an already-warm window fails in a cold one ("remembered
    screen shape changed"). Do not "fix" differing row counts between runs of
    the same day's query by widening settle thresholds: on this screen the
    count was rock steady within a run and rose with wall-clock time as
    G-MES added rows. (HISTORY.md Phase 82.18)

63. **`getRowCount()` is the count AFTER any client-side `Dataset.filter()`,
    and G-MES uses it.** `getRowCountNF()` is the count without the filter.
    Live: `dsQuickLinkInfo` reads 20 rows with `filterstr` `gubun!='RB'` and
    37 unfiltered; 5 of 681 datasets in one window had an active filter
    (shell datasets that day, but a report screen can do the same). A
    filtered result dataset shows - and this tool exports - fewer rows than
    it holds, with nothing else saying so; `js_read` now returns `filterstr`
    and the unfiltered count and the run warns. Nexacro documents
    `filter()` as changing what the Grid displays.

64. **The form walk used to fit only THREE work windows.** `_findForms()`
    stopped at 400 forms without saying so; measured, the shell alone is ~200
    and each work window ~47, so with 7-8 windows open a screen was read as
    having no filters and no division tree - a confident, wrong picture (the
    real cause behind "close the stale windows first" on P3111UM00 and
    Q2277UM00). The cap is 4000 (~58 windows), the walk reports when it was
    cut (`_findForms.truncated`), the discovery lists carry their true totals,
    and `discover()` raises on a cut reading. (HISTORY.md Phase 82.20)

65. **When a run reports zero rows, look at the OTHER grids before believing
    it.** A screen with a master and a detail grid, or a placeholder and a
    real one, can have the default pick empty while the report sits in
    another. `explain_empty_result()` now names every other grid holding rows
    and how many; it never picks one for you. Also: dates typed with `--set`
    are not checked against the rows (only `--from/--to` + `--verify` are) and
    the run says so. (HISTORY.md Phase 82.20)

66. **Do not choose a result grid by which one already has rows.** A screen
    can carry static tables (a "Formula" legend, a lookup list) populated from
    the moment it opens, while the real result grid is empty until Inquiry -
    and may build its columns only then (R3220UM00: `dsGrpSummary` reads "3
    cols, 0 rows" before the query and 1 row x 83 columns after; the 37-row
    `dsOperAnalCalc` beside it is a definitions table). Picking the populated
    one exports a perfectly clean, perfectly wrong file. The tool's own default
    was right; overriding it was the error. Tell-tale: the count was the same
    before and after Inquiry and never moved - `unchanged_result_note()` now
    says so for any grid not already proven by an earlier run, and such a result
    is never written to the profile. (HISTORY.md Phase 82.21)

67. **An unattended run has failure modes the interactive one never shows.**
    Found by running a batch through Windows Task Scheduler for real (HISTORY.md
    Phase 83): Python block-buffers stdout when it is redirected to a file, so a
    hung run leaves an EMPTY log (launch with `-u`); a failed sign-in still
    leaves the browser it started running, one per failed night, unless every
    exit path stops it; a freshly downloaded `.xlsx` can still be open in the
    DRM agent, antivirus or the browser, so the rename to its final name fails
    at once with WinError 32 (`replace_when_free()` polls while it is 32 or 5,
    and raises anything else immediately); and a click on a masked date field
    can race the first keystrokes, leaving text like `9196-0_-__` (`type_text()`
    retries with a longer settle, reading back every attempt). A scheduled task
    also runs only while its user is signed in to Windows - the DPAPI credential
    and the browser both need that session. Do not sign in repeatedly to
    re-test: after a handful of sign-ins in a short time the AD SSO window
    stopped opening; the tool refused to submit the saved password, correctly.
    (HISTORY.md Phase 83)

68. **A result's date may live inside a value, not in a column.** B3320UM00's
    summary has no date column - `baseDate` is empty on every row - and keeps the
    date as a KEY inside `jsonObj` (`{"20260919": {...}, "Total": {...}}`); the
    column headers ("2026-09-19") are built from it. `--verify jsonObj` therefore
    checks the dates named inside the values (single day: exactly that day; range:
    all inside it). Two more traits of that screen: its Period boxes display the
    date while the bound dataset row is empty (Phase 83.3), and its Detail grid
    fills only after a drill-down click on a summary number - an empty grid beside
    a populated one is not necessarily a failed query. (HISTORY.md Phase 83.3-83.4)

69. **A probe run leaves state that poisons the next run's own guard.** The
    unchanged-result check compares the result's row count before and after
    Inquiry. If an earlier run in the same browser already loaded the rows and
    left the work window open, the next run reuses that window, sees the same
    count before and after, and calls a correct answer "static content" - and
    refuses to save the profile (M1642UM00). Close the window (`run --close-tabs`;
    `describe --close-tabs` does not close it) and record again from a fresh screen.
    (HISTORY.md Phase 83.7)

70. **A screen's "period" can be many shapes - record what it offers, do not
    invent a day.** A month range written `YYYYMM` (M1642UM00, B3320UM00 by
    default); a date-time pair with an 08:00 boundary whose From box is derived
    and only To is editable (L5323UM00, a rolling seven days); two unbound masked
    boxes typed by hand (Q2241UM00, R3220UM00); bound boxes the profile stores as
    typed `sets` (Q2251UM00); no date at all - a live monitor (R3224WM00). For
    each, either name a column that proves the period (`--verify`, including a
    JSON key, gotcha 68) or say plainly that nothing can, and confirm by reading
    the screen. (HISTORY.md Phase 83.3, 83.7, 83.8)

71. **A `WM00`/`WM01` catalogue entry beside a `UM00` may be the same screen.**
    `R3225WM00` and `R3220UM00` share menu `FFM0521`; `L5323WM00`/`WM01` are the two
    Quick Views (Daily, Duration) of `L5323UM00`. Compare menu ids before
    recording a second copy. And the catalogue is the account's: a code that is not
    in it (`Q3124UM00`) simply is not there - `find` says 0 matches.
    (HISTORY.md Phase 83.6, 83.8)

72. **The first run on a brand-new browser profile is the slowest run it will
    ever have, and G-MES "ready" is not "everything loaded".** No cache: the
    whole Nexacro application and every screen's files come down through the
    corporate proxy, and a page busy building itself did not answer
    `Runtime.evaluate` for 20 s right after the credentials were submitted - while
    G-MES was in fact signing in. Waits must tolerate a busy page until their own
    deadline (`gmes_login.transient`), and an interrupted sign-in is re-checked, never
    blindly retried (the credentials may already have been sent). The copied
    session did NOT sign straight in on the one clean run tried; SSO with the saved
    credentials did, after one automatic retry. (HISTORY.md Phase 84.1, 84.10)

73. **A work-form's tab is recorded under its SHELL's menu id, not the
    catalogue's.** `R3224WM00` is menu `FFM0524` in the catalogue but opens nested in
    the tab of menu `FFM0520`; `gdsOpenMenu` records that tab under `FFM0520`, so
    waiting for `FFM0524` never succeeds - the screen is on screen and the tool says
    "did not open ... may not be permitted" 110 s later. The proof is the form's own
    file name, which starts with the exact code. (HISTORY.md Phase 84.2)

74. **`GMES_PROFILE_DIR` alone does not redirect a machine that has already run.**
    The profile recorded in `browser.json` wins. A clean-machine rehearsal needs
    `GMES_PROFILE_DIR` AND `GMES_BROWSER_STATE` at new locations, and neither may
    ever point at a real browser's profile. (HISTORY.md Phase 84.10)

75. **Windows paths break in three different places, at three different lengths.**
    Git for Windows refuses to clone into a 221-character path without
    `core.longpaths`; PowerShell cannot `cd` into it even with OS long paths on;
    Windows refuses any file over 259 characters when long paths are off, and this
    tool's longest file (a `.gmes-download-<uuid>_<title>...xlsx` staging name) plus
    the folder is ~170 characters before the project path. Titles are capped at 80,
    the preflight warns, and the project belongs in a short folder (`C:\gmes`), not
    a OneDrive one (sync clients hold fresh exports open: WinError 32).
    (HISTORY.md Phase 84.9, 84.15)

76. **A `.cmd` launcher has four traps.** A lone `%` is silently dropped from a
    literal; the file's code page decides what non-ASCII bytes mean (an ASCII file
    with `errors="replace"` turned an Arabic path into `????`); a `)` inside an
    expanded path ends a parenthesised block; a byte-order mark breaks the first
    line. Use `%~dp0` instead of writing the folder in, UTF-8 without BOM plus
    `chcp 65001`, `%%`, and no blocks - and test it under real `cmd.exe` started from
    code page 437, because a tester's console may already be UTF-8.
    (HISTORY.md Phase 84.11)

77. **Process numbers are recycled, so "the PID is alive" is not "the run is
    alive".** A stale lock whose PID Windows had handed to explorer or a browser
    refused every run forever; so did an empty lock left by a crash between creating
    it and writing the pid. A lock is stale when its process is gone, its
    executable is not Python, it is older than any run can last (8 h), or it is
    unreadable and older than 10 minutes. (HISTORY.md Phase 84.13)

78. **A cold profile's FIRST search is lost, and its screen shape arrives in
    pieces.** Nexacro creates the integrated-search panel lazily, so the first query
    after a cold sign-in reports `popup not created`; typing it again works. Forms
    and datasets also bind one after another, so "same counts on two polls" can fire
    on a partial shape - wait for the recorded fingerprint, not for quiet.
    (HISTORY.md Phase 84.3, 84.17)

79. **A hung tab is not repaired from outside.** `Runtime.enable` never answering
    means the G-MES tab is stuck while the browser still serves its endpoint.
    Replacing the tab (`/json/new`, then `/json/close/<id>`) made the whole browser
    exit. Close the AUTOMATION browser through `Browser.close` and relaunch on the
    same profile - the session lives in the profile. (HISTORY.md Phase 84.18)

80. **The first sign-in after a fresh profile or a page reload often fails once.**
    "The SSO window never opened", then the automatic retry succeeds - seen 3 of 3
    times on the clean-machine rehearsal. It is the reason `sign_in` retries; do not
    read the first failure as a wrong password, and never force `--allow-password-login`
    to get past it. (HISTORY.md Phase 74, 84.1)

81. **An error message must name what was observed, not a guess at the cause.** "The
    browser closed or crashed" was printed when only the tab's connection had been
    aborted and the browser was alive. `BrowserGone` now reports the exception and the
    CDP method in flight. (HISTORY.md Phase 84.4)

82. **Screen codes come in more shapes than the ones you have seen.** 129 of 810 real
    codes (`BB210UM00`, `M4A11UM00`, `P225AUM00`) were refused by a "letters then four
    digits" check, so those screens could be exported but never remembered. The one
    shared shape is `gmes_profile.looks_like_code` - letters and digits, a letter first,
    a digit somewhere, 5-16 characters. (HISTORY.md Phase 84.20)

83. **One column can be bound on several sub-forms of a screen** (a copy per view tab).
    `--set` resolves to the one that is visible; if none or several are, it is ambiguous.
    (HISTORY.md Phase 84.21)

84. **The screen's own client-side filter decides which rows exist.** A dataset can hold
    66 rows while a filter (`lvlNo < '4'`) shows 24; the export is the visible 24 and the
    run says so. And a date can live in a column NAME (`A20260920`), which `--verify`
    cannot check yet. (HISTORY.md Phase 84.22)

85. **A `--verify` refusal can mean the filter genuinely leaked another day's rows,
    not just "nothing to verify".** Q3211UM00's Period filter let through 8 of 23 rows
    dated the day after the requested range. Read WHY `--verify` refused before
    reaching for `--set` to route around it. (HISTORY.md Phase 84.23)

86. **A date column can carry an embedded time (`YYYYMMDDHHMMSS`) that exact-value
    `--verify` cannot match**, even when every row genuinely falls on the requested
    day. Recorded with `--set` and reported as such, not silently treated the same as
    "no date in the result". (HISTORY.md Phase 84.24)

87. **Not every screen fits the VD+date recipe.** Q3442UM00 (Quality Set Tracking)
    takes a single CN/SN/IMEI, not an org/period scope - every grid reads "No Data
    Found" until one is typed. Stop and ask rather than guess an identifier.
    (HISTORY.md Phase 84.25)

88. **A screen can pin its own export destination.** `--output-dir`/`--export`,
    when they genuinely differ from the tool's default, are remembered
    (`gmes_profile.save(output_dir=, export=)`) and reapplied on every later
    bare replay - CLI or the interactive front end's Replay - with no flags
    typed. An ordinary run of an unpinned screen never starts pinning one.
    (HISTORY.md Phase 84.28)

89. **"The export succeeded" and "the file is still there" can be different
    claims on a network share.** A share can allow creating a file while
    denying its later deletion or rename - confirm a live destination FROM the
    destination (`dir /a` on the share itself), not from the exporting
    process's own log. `--export both` failing there silently deleted an
    already-successful Excel file when the CSV step's rename was denied;
    `--export xlsx` alone survived. (HISTORY.md Phase 84.28)

88. **A result grid can defeat a text-based dialog click.** A "Check Result" column
    of literal "OK" values crowded out the "Save to Excel" dialog's own OK button -
    first because the DOM scan behind `find_elements()` capped at 40 matches and
    never reached the dialog, then because `click_control()` returns on the first
    match found and the grid's decoys were already on screen before the dialog even
    rendered. Fixed by scanning the whole page before trimming, and by targeting the
    dialog's own button id (`popupExcelExport.form.btnOk`) directly rather than a
    bare label. (HISTORY.md Phase 84.26)

90. **A scheduled task's console window is not about its output.** Under an
    interactive logon (required so the automation browser can open on the
    desktop), Task Scheduler shows a console window for whatever process it
    starts directly - redirecting that process's own stdout/stderr to a log
    file does not suppress the window, because the window belongs to the
    process, not to where it sends its output. `-Hidden` on
    `New-ScheduledTaskSettingsSet` does not fix this either - it only hides
    the task from Task Scheduler's own UI list. The fix is a VBScript wrapper,
    `WScript.Shell.Run(cmd, 0, True)`: `0` hides the window, and the `True`
    (wait for completion) is required or the wrapped program's real exit code
    is lost and every later run reports success regardless of what actually
    happened. (HISTORY.md Phase 87)

## The nightly job

```powershell
python gmes_daily_prodplan.py [--date YYYYMMDD] [--days-back N]
                              [--division VD] [--output-dir PATH]
                              [--no-csv] [--keep-open]
```

Steps, each verified rather than assumed:

1. Start Chrome on the profile copy; sign in via AD SSO if needed.
2. Clear Notice popups.
3. Write the plan date into `P1112WF00.dsFilterDVO`.
4. Tick the Division in the Org tree.
5. Click **Inquiry**; wait for the result count to settle above zero.
6. Confirm the returned rows carry the requested `planYmd`.
7. Click **Excel Download** → **OK** on the Save-to-Excel dialog.
8. Move the file to `Data Hub Folder/GMES/` as
   `Production Plan by Order(Line)_<YYYYMMDD>_<HHMMSS>.xlsx`.
9. Write a machine-readable CSV alongside it, filler rows removed.

Exit code 0 on success; 1 with a diagnostic screenshot on any failure.
