# CompuLab Lab PC Agent — Quick Start

## Ano ito
Maliit na program na tumatakbo sa **bawat lab PC** (hindi sa server). Naghihintay
ito ng "unlock" signal mula sa CompuLab server pagkatapos ma-verify ang isang
estudyante, at pag natanggap niya yun, pinapatakbo nito ang command na
nag-a-unlock ng **kanyang sariling** lock/kiosk screen.

## Bakit ganito ang setup (hindi PsExec/SSH)
- Walang admin password na naka-store sa server
- Gumagana kahit naka-disable ang admin shares/remote management
- Bawat PC "shared secret" lang ang kailangan, hindi login credentials

## Setup — isang beses per PC

1. I-copy ang buong `lab_pc_agent` folder sa bawat lab PC
   (hal. `C:\CompuLabAgent`)
2. I-edit ang `agent_config.json`:
   - `secret` — dapat **eksaktong pareho** sa `PC_AGENT_SHARED_SECRET` sa
     `compulab/settings.py` ng server
   - `port` — dapat pareho sa `PC_AGENT_PORT` sa server (default `5555`)
   - `unlock_command` — ang tunay na command na mag-a-unlock ng PC na iyon
     (basahin ang komento sa loob ng file — **importante ito**, may
     paliwanag doon kung bakit hindi pwedeng i-script ang native Windows
     lock screen, at ano ang gagawin niyo kung wala pa kayong kiosk overlay app)
3. I-test muna manually: `python agent.py`
4. Kapag gumana, i-set up itong tumakbo automatically sa startup
   (Task Scheduler o NSSM — nakadetalye sa loob ng `agent.py`)
5. I-allow sa Windows Firewall ang inbound connections papunta sa port na
   ginamit (default 5555) para sa `python.exe`

## Bagong bahagi: built-in na login form (walang kailangang hiwalay na reception PC)

Ang lock screen ay may sarili nang **login form** ngayon — dalawang textbox
(Student/Instructor ID at Reservation Code) at isang "Mag-login" na button,
direkta sa loob ng "PC LOCKED" screen mismo. Hindi na kailangan ng
hiwalay na computer sa reception para mag-check-in ang estudyante — sa
mismong naka-lock na PC nila mismo sila mag-i-input ng credentials.

Kapag pinindot ang "Mag-login":
1. Direktang kakausapin ng agent ang CompuLab server (`server_url` sa
   `agent_config.json`) sa endpoint na `/labs/api/pc-agent-login/`
2. Ganoon ding validation rules ang gamit dito tulad ng dati (valid ba ang
   reservation, tama ba ang ID, atbp.) — parehong logic, JSON lang ang daan
3. Kapag successful, awtomatikong mawawala ang lock screen — hindi na
   kailangan ng hiwalay na `/unlock` na tawag pa, dahil ang agent mismo ang
   direktang nag-uutos sa sarili niyang lock screen na magtago

**Bagong field sa `agent_config.json`:** `server_url` — dapat itong itugma
sa totoong address ng CompuLab server (hal. `http://192.168.1.10:8000` sa
totoong deployment; `http://127.0.0.1:8000` habang nagte-test sa parehong
laptop).

Ang `/unlock` at `/lock` na HTTP endpoints ng agent (na ginagamit ng
`labs/network.py` sa server) ay nananatili pa rin — kapaki-pakinabang pa rin
ito para sa mga labs na may hiwalay na reception terminal, o para sa manual
na admin override.

## Bagong hardening feature (Windows lockdown)

Kasama na ngayon sa agent ang dalawang layer ng proteksyon, na naka-on
habang naka-lock at naka-off pag naka-unlock:

1. **Hotkey block** — hinaharangan ang Windows key, Alt+Tab, Alt+Esc, at
   Ctrl+Esc, para hindi maka-alis sa lock screen papunta sa Start menu o
   task switcher.
2. **Task Manager disable** — hindi na kayang buksan ang Task Manager
   habang naka-lock, kahit sa pamamagitan ng Ctrl+Alt+Delete screen.

**Mahalagang paalala:** Hindi kayang i-block ng kahit anong script ang
Ctrl+Alt+Delete mismo (Winlogon ang humahawak nito, hindi applications —
sinasadya ito ni Microsoft). Ang nangyayari: kahit ma-access nila yung
Ctrl+Alt+Delete screen, hindi na gagana yung Task Manager doon dahil sa
registry policy na na-apply.

Ang mga feature na ito ay nasa `lab_pc_agent/hardening.py`, at
Windows-specific — walang epekto (safe no-op) kung tumakbo sa ibang OS,
kaya hindi ito sasablay habang tine-test mo muna sa ibang machine.

## Sa server side

Sa `compulab/settings.py`, siguraduhin tama ang mga ito:

```python
PC_AGENT_PORT = 5555
PC_AGENT_SHARED_SECRET = '<yung parehong secret sa lahat ng PC>'
PC_AGENT_TIMEOUT_SECONDS = 4
```

At siguraduhing may naka-set na tamang `ip_address` ang bawat `PC` record sa
database (via `labs/pc_edit_form.html` o admin panel) — dito babase ang
server kung saang IP magpapadala ng unlock signal.

## Pag-test

1. Patakbuhin ang agent sa isang PC
2. Sa browser (kahit saan sa parehong network), pumunta sa:
   `http://<IP-ng-PC>:5555/ping` — dapat makakita ka ng `{"ok": true, ...}`
3. Mag-login bilang estudyante sa CompuLab (yung PC login flow na
   tumatawag sa `unlock_pc()`) — dapat tumakbo yung `unlock_command` sa PC na
   yun.

## Bagong feature: PC activity log (window title + address bar tracking)

Habang naka-unlock ang isang PC, kada `activity_report_interval_seconds`
(default 8s, itakda sa `agent_config.json`) kinukuha ng agent ang title bar
text ng foreground/active window sa oras na iyon at pina-post niya ito sa
server (`/labs/api/pc-agent-activity/`). Para sa browser, karaniwang ganito
ang laman: `"Page Title - Browser Name"`.

Kung naka-install ang optional na `uiautomation` package (`pip install
uiautomation`), kinukuha rin ng agent ang **address bar URL** ng browser
(Chrome/Edge/Brave/Opera/Firefox) sa parehong oras, para tumpak/specific
ang pagkakakilanlan ng site — halimbawa, ang ChatGPT ay hindi naglalagay ng
"ChatGPT" sa title bar (yung pangalan lang ng conversation ang nakikita
doon), kaya kailangan ang URL para masiguradong "ChatGPT" talaga ang
nakalabas sa PC Activity Log, hindi "Other site". Kung hindi naka-install
ang `uiautomation`, gagana pa rin ang agent — babalik lang ito sa
paghula base sa title text lang, tulad ng dati.

**Mahalagang saklaw:**
- Window title text (isang standard Windows API call) at, kung available,
  ang address bar URL na lang ang binabasa — WALANG keystrokes,
  screenshots, o page HTML.
- Nire-record lang kapag naka-unlock/may naka-assign na estudyante sa PC
  (server-side, tine-tsek ang `PC.current_user`/`current_session`).
- Buong window title AT URL ang naka-store (walang ibinubura) — nakikita
  ng admin/in-charge lahat ng detalye. May karagdagang "Site" tag na lang
  na tama at specific (hal. "Chrome — ChatGPT") gamit ang URL, para madali
  makita kung anong site nang hindi kailangang basahin ang buong title.
- Makikita ang mga log sa admin/in-charge dashboard: **PC activity log**
  sa sidebar, o `/labs/pc-activity/`.
- Itakda ang `activity_report_interval_seconds` sa `0` sa
  `agent_config.json` para i-disable ang feature na ito nang tuluyan sa
  isang partikular na PC.

Bago i-deploy ito sa mga totoong lab PC, siguraduhing alam ng mga
estudyante/in-charge na naka-on ang ganitong monitoring (hal. sa lab rules
o disclaimer sa lock screen) — magandang practice ito kahit saang
institution-owned na monitoring setup.

## Kung walang kiosk/lock software pa

Kung wala pa talagang paraan ng pag-lock ng mga PC ngayon (walang kiosk
overlay app), gagana pa rin ang buong system — mare-record pa rin ang login
ng estudyante — pero walang mangyayaring aktwal na "unlock" hanggang
magkaroon kayo ng kiosk/lock software na kayang kontrolin ng agent.
