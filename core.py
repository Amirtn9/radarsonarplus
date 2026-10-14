import os
import json
import time
import io
import re
import shlex
import socket
import asyncio
import logging
import statistics
from datetime import datetime, timedelta, timezone

import requests
import paramiko
import jdatetime
import matplotlib
from cryptography.fernet import Fernet

from settings import KEY_FILE, AGENT_FILE_PATH, AGENT_PORT

# Persistent WebSocket pool (keeps connections open + auto-reconnect)
from ws_client import GLOBAL_WS_POOL

# 🔧 نسخه ۴.۲: استخر ترد مشترک (به جای استخر پیش‌فرض ۵ تردی asyncio)
from runtime import SHARED_EXECUTOR

# Attempt to import websockets (Critical for new agent)
try:
    import websockets
except ImportError:
    websockets = None

# Configure matplotlib backend (must be before importing FigureCanvasAgg)
matplotlib.use("Agg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg

logger = logging.getLogger(__name__)


# ==============================================================================
# 📅 DATE & TIME UTILS
# ==============================================================================
def get_tehran_datetime():
    """Get current Tehran time"""
    return datetime.now(timezone.utc) + timedelta(hours=3, minutes=30)


def get_jalali_str():
    """Get formatted Jalali date string"""
    tehran_now = get_tehran_datetime()
    j_date = jdatetime.datetime.fromgregorian(datetime=tehran_now)
    months = {
        1: "فروردین",
        2: "اردیبهشت",
        3: "خرداد",
        4: "تیر",
        5: "مرداد",
        6: "شهریور",
        7: "مهر",
        8: "آبان",
        9: "آذر",
        10: "دی",
        11: "بهمن",
        12: "اسفند",
    }
    return f"{j_date.day} {months[j_date.month]} {j_date.year} | {j_date.hour:02d}:{j_date.minute:02d}"


# ==============================================================================
# 🛠 HELPER UTILS
# ==============================================================================
def extract_safe_json(text):
    """Smart JSON extraction from text output"""
    try:
        text = (text or "").strip()
        if not text:
            return None

        if text.startswith("{") and text.endswith("}"):
            try:
                return json.loads(text)
            except Exception:
                pass

        matches = re.findall(r"(\{.*?\})", text, re.DOTALL)
        if matches:
            for m in reversed(matches):
                try:
                    return json.loads(m)
                except Exception:
                    continue

        return None
    except Exception:
        return None


# ==============================================================================
# 📊 PLOTTING
# ==============================================================================
def generate_plot(server_name, stats):
    """Generate resource usage plot"""
    if not stats:
        return None

    try:
        fig = Figure(figsize=(10, 5))
        ax = fig.add_subplot(111)

        times = [s.get("time_str") for s in stats]
        cpus = [s.get("cpu", 0) for s in stats]
        rams = [s.get("ram", 0) for s in stats]

        ax.plot(times, cpus, label="CPU (%)", color="red", linewidth=2)
        ax.plot(times, rams, label="RAM (%)", color="blue", linewidth=2)

        ax.set_title(f"Server Monitor: {server_name} (Last 24h)")
        ax.set_xlabel("Time")
        ax.set_ylabel("Usage %")
        ax.set_ylim(0, 100)
        ax.legend()
        ax.grid(True, linestyle="--", alpha=0.6)

        if len(times) > 10:
            step = max(1, len(times) // 8)
            ax.set_xticks(range(0, len(times), step))
            ax.set_xticklabels(times[::step], rotation=45)

        fig.tight_layout()
        buf = io.BytesIO()
        FigureCanvasAgg(fig).print_png(buf)
        buf.seek(0)
        return buf
    except Exception as e:
        logger.error("Plot error: %s", e)
        return None


# ==============================================================================
# 🧠 SERVER MONITOR CORE
# ==============================================================================
class ServerMonitor:
    # ---------------------------------------------------------
    # 🔌 INTERNAL SSH HELPER (Fallback)
    # ---------------------------------------------------------
    @staticmethod
    def get_ssh_client(ip, port, user, password):
        """Create SSH connection"""
        client = paramiko.SSHClient()
        # Default to known-host validation to avoid credential theft via SSH MITM.
        # Explicit trust-on-first-use is available for deployments without known_hosts.
        client.load_system_host_keys()
        try:
            client.load_host_keys(os.path.expanduser('~/.ssh/known_hosts'))
        except OSError:
            pass
        client.set_missing_host_key_policy(
            paramiko.AutoAddPolicy() if os.getenv('SONAR_SSH_TRUST_ON_FIRST_USE') == '1'
            else paramiko.RejectPolicy()
        )
        client.connect(ip, port=port, username=user, password=password, timeout=10)
        return client

    @staticmethod
    def _get_agent_script():
        """Returns monitor_agent.py content as a string (used for remote install via SSH)."""
        try:
            from pathlib import Path

            p = Path(__file__).resolve().with_name("monitor_agent.py")
            return p.read_text(encoding="utf-8")
        except Exception:
            return None

    @staticmethod
    def _run_ssh_command(ip, port, user, password, command, timeout=60):
        """Raw SSH command execution"""
        client = None
        try:
            client = ServerMonitor.get_ssh_client(ip, port, user, password)
            full_cmd = f"export DEBIAN_FRONTEND=noninteractive; {command}"
            _, stdout, stderr = client.exec_command(full_cmd, timeout=timeout)
            out = stdout.read().decode().strip()
            err = stderr.read().decode().strip()
            exit_code = stdout.channel.recv_exit_status()
            result = (out + "\n" + err).strip()
            return exit_code == 0, result if exit_code == 0 else f"exit={exit_code}: {result}"
        except paramiko.AuthenticationException:
            return False, "AUTH_FAILED"
        except Exception as e:
            return False, str(e)
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

    # ✅ سازگار با install_agent_service (قبلاً صدا زده می‌شد ولی وجود نداشت)
    @staticmethod
    def _ssh_exec_sync(ip, port, user, password, command, timeout=120):
        return ServerMonitor._run_ssh_command(ip, port, user, password, command, timeout=timeout)

    # ---------------------------------------------------------
    # 🚀 WEBSOCKET COMMAND RUNNER (New Architecture)
    # ---------------------------------------------------------
    @staticmethod
    async def ws_send_command(ip, ws_port, token, payload, timeout=10):
        """Send a command to the monitor agent using a persistent WebSocket."""
        if websockets is None:
            return {"error": "websockets lib missing"}

        try:
            from database import Database
            from runtime import run_sync
            dedicated_token, tls_cert = await run_sync(Database().get_agent_transport, str(ip), int(ws_port))
            if not dedicated_token or not tls_cert:
                return {"error": "Agent is not TLS-enrolled; reinstall/repair the agent"}
            return await GLOBAL_WS_POOL.request(
                ip=str(ip),
                port=int(ws_port),
                token=dedicated_token,
                payload=payload,
                timeout=float(timeout),
                retries=2,
                tls_certificate=tls_cert,
            )
        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    def agent_cli_command(link, size=None):
        """Legacy SSH CLI fallback against the managed unprivileged agent path.

        All user-provided config text is shell quoted, never interpolated raw.
        """
        if not isinstance(link, str) or not 0 < len(link) <= 65536:
            raise ValueError('Invalid agent CLI link length')
        args = ['runuser', '-u', 'sonar-agent', '--',
                '/opt/sonar-agent-venv/bin/python',
                '/opt/sonar-agent/monitor_agent.py', link]
        if size is not None:
            value = float(size)
            if not 0 <= value <= 10:
                raise ValueError('Invalid test size')
            args.append(str(value))
        return shlex.join(args)

    @staticmethod
    async def run_remote_command(ip, port, user, password, command, timeout=60):
        """Execute command via SSH (operations/maintenance)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        def _runner():
            return ServerMonitor._run_ssh_command(ip, port, user, password, command, timeout=timeout)

        if loop is None:
            return _runner()
        # 🔧 نسخه ۴.۲: قبلاً None بود (استخر پیش‌فرض ~۵ تردی) و یک کاربر
        # می‌توانست با ۵ تست همزمان کل ربات را قفل کند.
        return await loop.run_in_executor(SHARED_EXECUTOR, _runner)

    @staticmethod
    async def ws_test_config(ip, ws_port, token, link, size=0.5, timeout=60):
        """تست یک کانفیگ از طریق وب‌سوکت پایدار (نسخه ۴.۲).

        روش قدیمی: باز کردن یک سشن SSH جدید + اجرای `python3 monitor_agent.py`
        به ازای هر کانفیگ. هم کند بود، هم با چند کاربر همزمان به سقف
        MaxStartups سرور ایران می‌خورد و کل صف را می‌بست.

        ایجنت از قبل اکشن `test_config` را پشتیبانی می‌کند و تست را داخل
        خودش در یک ترد جدا اجرا می‌کند، پس وب‌سوکت آزاد می‌ماند.
        """
        payload = {"action": "test_config", "link": link, "size": float(size)}
        res = await ServerMonitor.ws_send_command(ip, ws_port, token, payload, timeout=timeout)

        if isinstance(res, dict) and "error" in res:
            return False, res

        return True, res

    @staticmethod
    async def check_full_stats_ws(ip, ws_port, password):
        """Get stats via WebSocket"""
        payload = {"action": "get_stats"}
        res = await ServerMonitor.ws_send_command(ip, ws_port, password, payload)

        if "error" in res:
            return {"status": "Offline", "error": res["error"], "uptime_sec": 0, "traffic_gb": 0}

        return {
            "status": "Online",
            "cpu": res.get("cpu", 0),
            "ram": res.get("ram", 0),
            "disk": res.get("disk", 0),
            "traffic_out": res.get("traffic_gb", 0),
            "uptime_str": res.get("uptime_str", "N/A"),
        }

    # ---------------------------------------------------------
    # 🛠 INSTALLATION & SETUP
    # ---------------------------------------------------------
    @staticmethod
    def install_agent_service(ip, port_ssh, user, password, ws_port):
        """Install/Update Sonar monitor agent via SSH."""
        AGENT_PATH = "/opt/sonar-agent/monitor_agent.py"
        VENV_DIR = "/opt/sonar-agent-venv"
        VENV_PY = f"{VENV_DIR}/bin/python"
        VENV_PIP = f"{VENV_DIR}/bin/pip"
        SERVICE_PATH = "/etc/systemd/system/sonar-agent.service"

        try:
            # Reject any shell-metacharacters before interpolating remote commands.
            import ipaddress
            try:
                ipaddress.ip_address(str(ip))
            except ValueError:
                if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', str(ip)):
                    return False, 'Invalid server address'
            if not 1024 <= int(ws_port) <= 65535:
                return False, 'Invalid agent port'
            # One strong per-server token, not the SSH password.
            from database import Database
            token = Database().get_or_create_agent_token(ip, port_ssh)
            # 1) Upload agent
            script = ServerMonitor._get_agent_script()
            if not script:
                return False, "monitor_agent.py not found near this file (cannot upload agent)."

            upload_cmd = (
                "install -d -m 0755 /opt/sonar-agent\n"
                f"cat > {AGENT_PATH} <<'PY'\n"
                f"{script}\n"
                "PY\n"
                f"chmod 755 {AGENT_PATH}\n"
            )
            ok, out = ServerMonitor._ssh_exec_sync(ip, port_ssh, user, password, upload_cmd, timeout=120)
            if not ok:
                return False, f"Upload agent failed: {out}"

            # 2) Ensure venv + deps
            setup_cmd = (
                "bash -lc 'set -e\n"
                "export DEBIAN_FRONTEND=noninteractive\n"
                "apt-get update -y\n"
                "apt-get install -y python3 python3-venv ca-certificates curl openssl\n"
                f"if [ ! -d {VENV_DIR} ]; then python3 -m venv {VENV_DIR}; fi\n"
                f"{VENV_PY} -m pip install --upgrade pip\n"
                f"{VENV_PIP} install websockets==13.1 psutil==6.1.1\n"
                "id -u sonar-agent >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/sonar-agent --shell /usr/sbin/nologin sonar-agent\n"
                "chown -R sonar-agent:sonar-agent /opt/sonar-agent /var/lib/sonar-agent\n"
                "'"
            )
            ok, out = ServerMonitor._ssh_exec_sync(ip, port_ssh, user, password, setup_cmd, timeout=600)
            if not ok:
                return False, f"Agent dependency setup failed: {out}"

            # 3) Systemd service
            service_content = (
                "[Unit]\n"
                "Description=Sonar Monitor Agent\n"
                "After=network.target\n\n"
                "[Service]\n"
                "Type=simple\n"
                "User=sonar-agent\n"
                "Group=sonar-agent\n"
                "WorkingDirectory=/var/lib/sonar-agent\n"
                "EnvironmentFile=/etc/sonar-agent.env\n"
                f"ExecStart={VENV_PY} -u {AGENT_PATH} {int(ws_port)}\n"
                "NoNewPrivileges=true\n"
                "ProtectSystem=strict\n"
                "ProtectHome=true\n"
                "PrivateTmp=true\n"
                "ReadWritePaths=/var/lib/sonar-agent\n"
                "LimitNOFILE=4096\n"
                "StandardOutput=journal\n"
                "StandardError=journal\n"
                "SyslogIdentifier=sonar-agent\n"
                # 🔧 نسخه ۴.۳: ایجنت با اولویت پایین اجرا می‌شود و حداکثر ۶۰٪
                # از CPU را می‌گیرد. روی نودهایی که ربات هم همان‌جاست، این خط
                # جلوی خوابیدن کل ماشین موقع تست کانفیگ را می‌گیرد.
                "Nice=15\n"
                "IOSchedulingClass=idle\n"
                "CPUWeight=20\n"
                f"CPUQuota={os.getenv('SONAR_AGENT_CPU_QUOTA', '60%')}\n"
                "Environment=SONAR_AGENT_MAX_TESTS=3\n"
                "Restart=always\n"
                "RestartSec=3\n\n"
                "[Install]\n"
                "WantedBy=multi-user.target\n"
            )
            # The environment file is root-owned. A dedicated service user runs the agent.
            # The service uses a remote bind only after auth is explicitly installed.
            import shlex
            # Generate a unique certificate tied to this node's configured IP/DNS.
            import ipaddress
            try:
                ipaddress.ip_address(str(ip))
                san = f"IP:{ip}"
            except ValueError:
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", str(ip)):
                    return False, "Invalid agent hostname"
                san = f"DNS:{ip}"
            cert_setup = (
                "install -d -m 0750 -o root -g sonar-agent /etc/sonar-agent\n"
                "if [ ! -s /etc/sonar-agent/tls.crt ] || [ ! -s /etc/sonar-agent/tls.key ]; then\n"
                "  openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 3650 "
                f"-subj '/CN=sonar-agent' -addext 'subjectAltName={san}' "
                "-keyout /etc/sonar-agent/tls.key -out /etc/sonar-agent/tls.crt >/dev/null 2>&1\n"
                "fi\n"
                "chown root:sonar-agent /etc/sonar-agent/tls.key /etc/sonar-agent/tls.crt\n"
                "chmod 0640 /etc/sonar-agent/tls.key /etc/sonar-agent/tls.crt\n"
                "openssl x509 -in /etc/sonar-agent/tls.crt -noout -checkend 604800 >/dev/null\n"
            )
            ok, out = ServerMonitor._ssh_exec_sync(ip, port_ssh, user, password, cert_setup, timeout=90)
            if not ok:
                return False, f"Agent TLS provisioning failed: {out}"
            service_env = (f"SONAR_AGENT_TOKEN={token}\nSONAR_AGENT_BIND=0.0.0.0\n"
                           "SONAR_AGENT_TLS_CERT=/etc/sonar-agent/tls.crt\n"
                           "SONAR_AGENT_TLS_KEY=/etc/sonar-agent/tls.key\n")
            service_cmd = (
                f"cat > /etc/sonar-agent.env <<'ENV'\n{service_env}ENV\n"
                "chmod 600 /etc/sonar-agent.env\n"
                f"cat > {SERVICE_PATH} <<'SERVICE'\n{service_content}\nSERVICE\n"
                "systemctl daemon-reload\n"
                "systemctl enable --now sonar-agent\n"
                "systemctl restart sonar-agent\n"
                "systemctl --no-pager --full status sonar-agent || true\n"
            )
            ok, out = ServerMonitor._ssh_exec_sync(ip, port_ssh, user, password, service_cmd, timeout=180)
            if not ok:
                return False, f"Agent service setup failed: {out}"

            # 4) Healthcheck
            check_cmd = (
                f"ss -lntp | grep -q :{int(ws_port)} || "
                f"(echo 'Port {int(ws_port)} is not listening'; ss -lntp; exit 6); "
                "systemctl is-active --quiet sonar-agent || "
                "(echo 'sonar-agent is not active'; systemctl --no-pager --full status sonar-agent || true; exit 7)"
            )
            ok, out = ServerMonitor._ssh_exec_sync(ip, port_ssh, user, password, check_cmd, timeout=60)
            if not ok:
                ok2, logs = ServerMonitor._ssh_exec_sync(
                    ip, port_ssh, user, password, "journalctl -u sonar-agent -n 120 --no-pager || true", timeout=60
                )
                return False, f"Agent healthcheck failed: {out}\n\n--- sonar-agent logs ---\n{logs if ok2 else ''}"

            ok, cert = ServerMonitor._ssh_exec_sync(
                ip, port_ssh, user, password, 'cat /etc/sonar-agent/tls.crt', timeout=30)
            if not ok:
                return False, 'Could not retrieve enrolled agent TLS certificate over SSH'
            from database import Database
            Database().store_agent_certificate(ip, port_ssh, cert.strip())
            return True, f"Agent installed and TLS-enrolled on WSS:{int(ws_port)}"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def get_bot_public_ip():
        """Get bot's public IP"""
        try:
            services = ["https://api.ipify.org", "https://ifconfig.me/ip"]
            for url in services:
                try:
                    resp = requests.get(url, timeout=5)
                    if resp.status_code == 200:
                        return resp.text.strip()
                except Exception:
                    continue
            return None
        except Exception:
            return None

    @staticmethod
    def whitelist_bot_ip(target_ip, port, user, password, bot_ip, agent_port=None):
        """Allow ONLY the selected agent port from the verified bot public IP.

        Uses UFW only when it is enabled; never installs a blanket iptables ACCEPT.
        """
        import ipaddress
        try:
            source = str(ipaddress.ip_address(bot_ip))
            agent_port = int(agent_port)
            if not 1024 <= agent_port <= 65535:
                return False, 'Invalid agent port'
        except (ValueError, TypeError):
            return False, 'Invalid public IP/port'
        command = ("if command -v ufw >/dev/null 2>&1 && "
                   "ufw status | grep -q 'Status: active'; then "
                   f"ufw allow from {shlex.quote(source)} to any port {agent_port} proto tcp; "
                   "else echo 'Firewall not active; WSS still required'; fi")
        return ServerMonitor._run_ssh_command(target_ip, port, user, password, command, timeout=25)

    # ---------------------------------------------------------
    # 📡 MONITORING & TOOLS (Legacy SSH Fallback & New Tools)
    # ---------------------------------------------------------
    @staticmethod
    def check_full_stats(ip, port, user, password):
        """Legacy SSH stats check"""
        try:
            cmd = (
                "echo $(grep 'cpu ' /proc/stat | awk '{usage=($2+$4)*100/($2+$4+$5)} END {print usage}')"
                "_$(free -m | awk 'NR==2{printf \"%.2f\", $3*100/$2 }')"
            )
            ok, out = ServerMonitor._run_ssh_command(ip, port, user, password, cmd, 10)
            if ok:
                parts = out.split("_")
                return {"status": "Online", "cpu": float(parts[0]), "ram": float(parts[1])}

            if str(out) == "AUTH_FAILED":
                return {"status": "Offline", "error": "Auth Failed"}
            return {"status": "Offline", "error": "SSH Error"}
        except Exception:
            return {"status": "Offline", "error": "Connect Fail"}

    @staticmethod
    async def install_speedtest(ip, port, user, password):
        cmd = "apt-get install -y speedtest-cli || pip3 install speedtest-cli"
        return await ServerMonitor.run_remote_command(ip, port, user, password, cmd, timeout=300)

    @staticmethod
    async def run_speedtest(ip, port, user, password):
        return await ServerMonitor.run_remote_command(ip, port, user, password, "speedtest-cli --simple", timeout=90)

    @staticmethod
    async def clear_cache(ip, port, user, password):
        return await ServerMonitor.run_remote_command(
            ip, port, user, password, "sync; echo 3 > /proc/sys/vm/drop_caches", timeout=10
        )

    @staticmethod
    async def clean_disk_space(ip, port, user, password):
        cmd = "apt-get autoremove -y && apt-get clean && journalctl --vacuum-time=1d && rm -rf /tmp/*"
        return await ServerMonitor.run_remote_command(ip, port, user, password, cmd, timeout=60)

    @staticmethod
    async def set_dns(ip, port, user, password, dns_type):
        dns_map = {
            "google": "nameserver 8.8.8.8\nnameserver 8.8.4.4",
            "cloudflare": "nameserver 1.1.1.1\nnameserver 1.0.0.1",
            "shecan": "nameserver 178.22.122.100\nnameserver 185.51.200.2",
        }
        if dns_type not in dns_map:
            return False, "Invalid DNS"
        cmd = f"echo '{dns_map[dns_type]}' > /etc/resolv.conf"
        return await ServerMonitor.run_remote_command(ip, port, user, password, cmd, timeout=10)

    @staticmethod
    async def full_system_update(ip, port, user, password):
        cmd = "apt-get update -y && apt-get upgrade -y"
        return await ServerMonitor.run_remote_command(ip, port, user, password, cmd, timeout=600)

    @staticmethod
    async def repo_update(ip, port, user, password):
        cmd = "apt-get update -y"
        return await ServerMonitor.run_remote_command(ip, port, user, password, cmd, timeout=120)

    # ---------------------------------------------------------
    # 🌍 GLOBAL PING & API TOOLS
    # ---------------------------------------------------------
    @staticmethod
    def check_host_api(target):
        try:
            headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
            url = f"https://check-host.net/check-ping?host={target}&max_nodes=50"
            req = requests.get(url, headers=headers, timeout=10)
            if req.status_code != 200:
                return False, f"API Error: {req.status_code}"

            request_id = req.json().get("request_id")
            result_url = f"https://check-host.net/check-result/{request_id}"

            poll_data = {}
            for _ in range(8):
                time.sleep(2.5)
                res_req = requests.get(result_url, headers=headers, timeout=10)
                poll_data = res_req.json()
                if isinstance(poll_data, dict):
                    completed = sum(1 for _, v in poll_data.items() if v)
                    if completed >= 10:
                        break

            return True, poll_data
        except Exception as e:
            return False, str(e)

    @staticmethod
    def format_check_host_results(data):
        if not isinstance(data, dict):
            return "❌ داده نامعتبر"

        ir_city_map = {
            "ir1": "Tehran (MCI)",
            "ir-mci": "Tehran (MCI)",
            "ir-mtn": "Tehran (Irancell)",
            "ir-tci": "Tehran (Mokhaberat)",
            "ir-teh": "Tehran (Afranet)",
            "ir-thr": "Tehran (DC)",
            "ir-afn": "Tehran (Afranet)",
            "ir-hiw": "Tehran (HiWeb)",
            "ir-mbn": "Tehran (MobinNet)",
            "ir-rsp": "Tehran (Respina)",
            "ir-ztn": "Tehran (Zitel)",
            "ir-pt": "Tehran (Parstabar)",
            "ir2": "Tabriz (Shatel)",
            "ir-tbz": "Tabriz (Shatel)",
            "ir3": "Karaj (Asiatech)",
            "ir-krj": "Karaj (Asiatech)",
            "ir4": "Shiraz (ParsOnline)",
            "ir-shz": "Shiraz (ParsOnline)",
            "ir5": "Mashhad (Ferdowsi)",
            "ir-mhd": "Mashhad (HostIran)",
            "ir6": "Isfahan (Mokhaberat)",
            "ir-ifn": "Isfahan (Mokhaberat)",
            "ir-ahw": "Ahvaz (Mokhaberat)",
            "ir-qom": "Qom (Asiatech)",
        }

        rows = []
        has_iran = False

        for node, result in data.items():
            if not result or not isinstance(result, list) or len(result) == 0 or not result[0]:
                continue

            try:
                if node[:2].lower() != "ir":
                    continue

                has_iran = True
                node_clean = node.split(".")[0].lower()

                city_name = "Tehran"
                for key, val in ir_city_map.items():
                    if key in node_clean:
                        city_name = val
                        break

                location_display = f"🇮🇷 {city_name}"
                packets = result[0]
                rtts = []

                for p in packets:
                    if p[0] == "OK":
                        rtts.append(p[1] * 1000)

                if rtts:
                    ping_stat = f"{min(rtts):.0f}/{statistics.mean(rtts):.0f}/{max(rtts):.0f}"
                else:
                    ping_stat = "Timeout"

                rows.append(f"`{location_display.ljust(15)}` | `{ping_stat}`")
            except Exception:
                continue

        if not has_iran:
            return "⚠️ هیچ سرور فعالی از ایران یافت نشد."

        return (
            "🌍 **Check-Host (Iran)**\n"
            "`Location       | Latency (min/avg/max)`\n"
            + "─" * 45
            + "\n"
            + "\n".join(rows)
        )

    @staticmethod
    def format_iran_ping_stats(check_host_data):
        return ServerMonitor.format_check_host_results(check_host_data)

    @staticmethod
    def make_bar(percentage, length=10):
        if not isinstance(percentage, (int, float)):
            percentage = 0
        if percentage < 0:
            percentage = 0
        if percentage > 100:
            percentage = 100

        blocks = "▏▎▍▌▋▊▉█"
        full_blocks = int((percentage / 100) * length)
        remainder = (percentage / 100) * length - full_blocks
        idx = int(remainder * len(blocks))
        if idx >= len(blocks):
            idx = len(blocks) - 1

        bar = "█" * full_blocks
        if full_blocks < length:
            bar += blocks[idx] + " " * (length - full_blocks - 1)
        return bar


# ==============================================================================
# 🔐 SECURITY HELPER
# ==============================================================================
class Security:
    def __init__(self):
        if not os.path.exists(KEY_FILE):
            # O_EXCL prevents symlink attacks and ensures restrictive permissions.
            import errno
            try:
                fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as f:
                    f.write(Fernet.generate_key())
            except FileExistsError:
                pass
        if os.path.islink(KEY_FILE):
            raise PermissionError('Refusing symlinked encryption key')
        os.chmod(KEY_FILE, 0o600)

        with open(KEY_FILE, "rb") as f:
            self.key = f.read()

        self.cipher = Fernet(self.key)

    def encrypt(self, txt):
        return self.cipher.encrypt(txt.encode()).decode()

    def decrypt(self, txt):
        try:
            return self.cipher.decrypt(txt.encode()).decode()
        except Exception:
            return ""


# Initialize global security instance
sec = Security()
