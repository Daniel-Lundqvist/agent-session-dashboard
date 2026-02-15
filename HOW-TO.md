# Agent Session Dashboard — How-To

## Starta dashboarden

```bash
cd ~/projects/lab/agent-session-dashboard
python3 scripts/start_dashboard.py
```

En tray-ikon (terminalikon) dyker upp i Xfce-panelen nere vid klockan.
Dashboarden autostartar vid inloggning (konfigurerat via `~/.config/autostart/`).

---

## Använda dashboarden (GUI)

### Klicka på tray-ikonen
En meny visas med:
- Lista på aktiva sessioner med statusikoner
- **Ny session...** — öppnar dialog där du anger projektnamn + arbetsmapp
- **Visa alla (split)** — öppnar alla sessioner i ett auto-rutnät
- **Visa markerade** — bocka checkboxar på de sessioner du vill se, klicka sedan denna
- **Remote (ttyd)** — undermeny för att starta/stoppa ttyd per session
- **Stoppa alla sessioner** — avslutar allt

### Statusikoner
| Ikon | Betydelse |
|------|-----------|
| 🔵 | Jobbar — Claude Code bearbetar något |
| 🟡 | Väntar på svar — Claude ställer en fråga eller visar val |
| ⚪ | Redo — Claude väntar på input (idle) |
| 🔴 | Fel — något gick snett |
| ⚫ | Stoppad |

### Split view
- **2 sessioner** → sida vid sida
- **3 sessioner** → 2 ovan + 1 nedan (full bredd)
- **4 sessioner** → 2x2 rutnät
- **5+** → automatiskt rutnät

### Remote-åtkomst (mobil via Tailscale)
1. Klicka tray-ikonen → **Remote (ttyd)** → **Starta ttyd: projektnamn**
2. En URL visas, t.ex. `http://100.x.x.x:7681`
3. Öppna den i webbläsaren på din telefon (via Tailscale-nätet)
4. Du ser terminalen live och kan skriva i den

---

## Instruktioner för Tess (Agent Zero)

### Grundläggande setup

Innan du kan använda dashboarden, importera `AgentManager`:

```python
import sys
sys.path.insert(0, "/home/dalu/projects/lab/agent-session-dashboard")
from src.agent_manager import AgentManager

manager = AgentManager()
```

### Skapa en session

När du får en uppgift av Dalu som kräver Claude Code, skapa en session:

```python
session = manager.create_session("projektnamn", "/home/dalu/projects/projektnamn")
```

Detta:
- Skapar en tmux-session med namnet `claude_projektnamn`
- Startar `claude --dangerously-skip-permissions` i den
- Dalu kan se sessionen i tray-ikonen och klicka fram den

### Skicka kommandon

```python
# Skicka en text-prompt till Claude Code
manager.send_command("claude_projektnamn", "Fixa bugen i auth.py")

# Skicka råa tangenter (för interaktiva val, Ctrl+C, etc.)
manager.send_keys("claude_projektnamn", "1")          # Välj alternativ 1
manager.send_keys("claude_projektnamn", "C-c")        # Ctrl+C
manager.send_keys("claude_projektnamn", "y", enter=True)  # Svara "y" + Enter
```

### Läsa output

```python
output = manager.capture_output("claude_projektnamn")
print(output)  # Visar exakt vad terminalen visar just nu
```

### Kontrollera status

```python
from src.session_state import SessionState

session_info = manager.get_session(session_name)
if session_info.state == SessionState.WAITING_INPUT:
    # Claude väntar på svar — läs output och avgör vad som ska svaras
    output = manager.capture_output(session_name)
    # Analysera output och svara...
elif session_info.state == SessionState.IDLE:
    # Claude är redo för nästa kommando
    pass
elif session_info.state == SessionState.WORKING:
    # Claude jobbar fortfarande — vänta
    pass
```

### Vänta på att Claude blir klar

```python
import time

def wait_for_idle(manager, session_name, timeout=120):
    """Vänta tills Claude är klar och redo."""
    start = time.time()
    while time.time() - start < timeout:
        info = manager.get_session(session_name)
        if info and info.state in (SessionState.IDLE, SessionState.WAITING_INPUT):
            return info.state
        time.sleep(2)
    return None

# Använd så här:
manager.send_command("claude_projektnamn", "Skapa en REST API med FastAPI")
state = wait_for_idle(manager, "claude_projektnamn", timeout=180)

if state == SessionState.WAITING_INPUT:
    # Claude frågar något — läs och svara
    output = manager.capture_output("claude_projektnamn")
    # ...analysera och svara
elif state == SessionState.IDLE:
    # Klart!
    output = manager.capture_output("claude_projektnamn")
```

### Hantera interaktiva val

Claude Code visar ibland flervalsfrågor. Så här hanterar du dem:

```python
output = manager.capture_output("claude_projektnamn")

# Om output innehåller numrerade alternativ:
# 1. Skapa ny fil
# 2. Redigera befintlig
# 3. Avbryt
if "1." in output and "2." in output:
    manager.send_keys("claude_projektnamn", "1")  # Välj alternativ 1
```

### Lista och hantera sessioner

```python
# Lista alla aktiva sessioner
sessions = manager.list_sessions()
for s in sessions:
    print(f"{s.name}: {s.state.value}")

# Avsluta en session
manager.kill_session("claude_projektnamn")

# Avsluta alla
manager.kill_all_sessions()
```

### Aktivera remote-åtkomst (ttyd)

```python
# Starta ttyd för en session (Dalu kan se den från telefonen)
port = manager.start_ttyd("claude_projektnamn")
url = manager.get_ttyd_url("claude_projektnamn")
print(f"Remote URL: {url}")  # http://100.x.x.x:7681

# Stoppa ttyd
manager.stop_ttyd("claude_projektnamn")
```

### Komplett exempel: uppgift från Dalu

```python
import sys, time
sys.path.insert(0, "/home/dalu/projects/lab/agent-session-dashboard")
from src.agent_manager import AgentManager
from src.session_state import SessionState

manager = AgentManager()

# 1. Skapa session för projektet
project = "web-scraper"
project_dir = "/home/dalu/projects/web-scraper"
manager.create_session(project, project_dir)
session_name = f"claude_{project}"

# 2. Vänta tills Claude Code har startat
time.sleep(5)

# 3. Skicka uppgiften
manager.send_command(session_name, "Bygg en web scraper med BeautifulSoup som hämtar nyheter från DN.se")

# 4. Övervaka tills klart
while True:
    time.sleep(3)
    info = manager.get_session(session_name)
    if not info:
        break

    if info.state == SessionState.IDLE:
        print("Claude är klar!")
        output = manager.capture_output(session_name)
        print(output)
        break

    elif info.state == SessionState.WAITING_INPUT:
        output = manager.capture_output(session_name)
        print(f"Claude frågar: {output[-500:]}")
        # Här kan du analysera frågan och svara,
        # eller eskalera till Dalu
        break
```

---

## Felsökning

### Dashboarden syns inte i panelen
```bash
# Kontrollera att den kör
ps aux | grep start_dashboard

# Starta manuellt
python3 ~/projects/lab/agent-session-dashboard/scripts/start_dashboard.py
```

### tmux-session finns inte
```bash
# Lista tmux-sessioner
tmux list-sessions

# Anslut manuellt
tmux attach-session -t claude_projektnamn
```

### ttyd startar inte
```bash
# Kontrollera att ttyd finns
which ttyd

# Testa manuellt
ttyd --writable --port 7681 tmux attach-session -t claude_projektnamn
```
