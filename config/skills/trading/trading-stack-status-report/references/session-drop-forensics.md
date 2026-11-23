# Why did the broker session die? (forensics recipe)

Answers "로그인이 왜 끊겼어?" with evidence instead of a guess. The bridge logs only prove the session
IS gone; the cause lives in the Windows event log and in the client's own launcher log. Run these five
probes in one batch, in this order.

## 1. The vendor component's crash (the usual answer)

```powershell
$s=Get-Date '<window start>'; $e=Get-Date '<window end>'
Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=$s; EndTime=$e} -ErrorAction SilentlyContinue |
  Where-Object { $_.Message -match 'DIBSERVER|CPSTART|coMain|Cybos|CREON' } |
  Select-Object TimeCreated,Id,LevelDisplayName,ProviderName,@{n='M';e={($_.Message -replace '\s+',' ')}} |
  Format-List | Out-String -Width 220
```

- `Id 1000` = Application Error: names the faulting **program** and **module**. `Id 1001` = Windows Error
  Reporting `APPCRASH` for the same event.
- Measured signature: `DIBSERVER.EXE` faulting **in itself** with exception code `0xc0000005` (access
  violation). That component is the CybosPlus COM server the bridge attaches to — when it dies the
  session is gone and **restarting the bridge cannot fix it**; only a full Creon restart + re-login can.
- Add the agent/locale caveat: the console renders Korean weekdays as mojibake (`오후 1:04` reads as
  `???? 1:04`), so confirm AM/PM against your own filter window rather than the label.

## 2. Is it a one-off or chronic?

```powershell
Get-WinEvent -FilterHashtable @{LogName='Application'; Id=1000; StartTime=(Get-Date).AddDays(-7)} |
  Where-Object { $_.Message -match 'DIBSERVER' } |
  Group-Object { $_.TimeCreated.ToString('yyyy-MM-dd') } | Sort-Object Name |
  ForEach-Object { $_.Name + ' : ' + $_.Count + ' (' + (($_.Group | % { $_.TimeCreated.ToString('HH:mm:ss') }) -join ', ') + ')' }
```

A rising count across days (`1 → 2 → 4`) is a worsening vendor bug, not a one-off — say so, because it
tells the owner the countermeasure is line/environment stability rather than a code change.

## 3. Correlate with the link — as correlation, never as cause

The Intel Wi-Fi driver logs `Netwtw06` info events `6000 - BSS missed beacons`. Info level means they are
ignored by default and are easy to dismiss; a burst in the minutes before a crash is the only link-side
signal there is. Count them in a ±7 min window around each crash, then compare against a quiet hour:

```powershell
Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Netwtw06'; StartTime=$s; EndTime=$e} |
  Group-Object { $_.TimeCreated.ToString('HH') } | Sort-Object Name |
  ForEach-Object { $_.Name + '시 : ' + $_.Count + '건' }
```

Measured: 166 events in the crash hour vs 10-26 per hour in quiet hours, with the beacon burst ~36 s
before the crash; the same pattern repeated at each of the day's crashes. The faulting module is the
vendor's own exe, so the supported claim is "crash and link flapping land in the same minutes" — say
plainly that causation is not provable from outside the component.

## 4. The client's own record of the recovery

- `C:\CREON\STARTER\ncstarter.log` (cp949): `CStarterDlg::DoSignOnCybos 사인온 성공` = the sign-on that
  actually took. Pair it with the bridge's `=== log opened pid=…` line and the `/health` transition to
  show the recovery sequence: Creon exit → updater → sign-on → bridge connect.
- The bridge's supervisor log shows the same thing from our side: `HALF-DEAD: /health connected but
  /balance failed` typically appears **minutes before** the crash, then `Creon not running - stopping
  the bridge`, `bridge connected and answering broker queries` after the re-login.
- The trading loop's own reaction is second-order: it halts after its consecutive check failures, so the
  halt time is `crash + N x cycle interval` — do not report it as the cause.

## 5. When nothing lines up

If the crash, the client log and the re-login times do not correlate, report 'cause unknown' with the
probes you ran. Never promote a hypothesis to a cause to make the report tidy.

## What to do about it (report, not a script)

- A vendor COM-server crash is not fixable by our code: the countermeasures are line stability (wired or
  a stable AP instead of a flapping hotspot; keep the phone awake) and a fast human re-login.
- Until the re-login, no exit and no entry can be placed — state the exposure, not a P&L, for that window.
- Make sure a half-dead bridge (health true + `/balance` failing) reaches the owner as an alert, because
  it is the earliest signature of this failure.
