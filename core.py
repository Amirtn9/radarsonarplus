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
from ssh_support import connect_ssh, describe_ssh_error
from access_policy import valid_agent_port

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
    return datetime.now(__import__('zoneinfo').ZoneInfo('Asia/Tehran'))


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
def format_speed(value):
    if value is None:return 'اندازه‌گیری نشده'
    try:return f'{float(value):g} Mbps'
    except (TypeError,ValueError):return 'نامعلوم'


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
        return connect_ssh(ip, port, user, password)

    @staticmethod
    def check_ssh_connection(ip, port, user, password):
        ok, detail = ServerMonitor._run_ssh_command(ip,port,user,password,'printf SONAR_SSH_OK',20)
        return {'status':'Online'} if ok and 'SONAR_SSH_OK' in detail else {'status':'Offline','error':detail or 'SSH_CHECK_FAILED'}

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
            return False, "AUTH_FAILED: نام کاربری، رمز یا روش ورود SSH پذیرفته نشد."
        except Exception as e:
            return False, describe_ssh_error(e)
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

    # ✅ سازگار با install_agent_service (قبلاً صدا زده می‌شد ولی وجود نداشت)
    @staticmethod
    def _ssh_exec_sync(ip, port, user, password, command, timeout=120):
        from agent_deploy import privileged_script
        return ServerMonitor._run_ssh_command(ip, port, user, password, privileged_script(command), timeout)

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
    async def agent_request(monitor, link, size=0.5, timeout=60, uid=None):
        from tunnel_logic import tunnel_manager
        async with tunnel_manager._user_sem(uid or monitor.get('owner_id')), tunnel_manager.global_sem:
            port=int(monitor.get('ws_port') or AGENT_PORT)
            if str(link).startswith(('https://','http://')):
                result=await ServerMonitor.ws_send_command(monitor['ip'],port,None,{'action':'fetch_sub','link':link},timeout)
                if result.get('error'): return False,json.dumps(result,ensure_ascii=False)
                return True,json.dumps({'type':'sub','configs':result.get('links',[]),'sub_info':result.get('sub_info',{})},ensure_ascii=False)
            ok,result=await ServerMonitor.ws_test_config(monitor['ip'],port,None,link,size=size,timeout=timeout)
            return ok,json.dumps(result,ensure_ascii=False)

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

        return {**res, 'status':'Online', 'transport':'wss', 'agent_status':'Online',
                'traffic_out':res.get('traffic_gb',0)}

    # ---------------------------------------------------------
    # 🛠 INSTALLATION & SETUP
    # ---------------------------------------------------------
    @staticmethod
    def install_agent_service(ip, port_ssh, user, password, ws_port, progress=None):
        from agent_deploy import deploy_agent
        from database import Database
        try:
            return deploy_agent(ServerMonitor, Database(), ip, port_ssh, user, password, ws_port, progress=progress)
        except (ValueError, LookupError) as exc:
            return False, str(exc)

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
        import ipaddress
        try:
            source = str(ipaddress.ip_address(bot_ip))
            agent_port = valid_agent_port(agent_port)
        except (ValueError, TypeError):
            return False, 'Invalid public IP/port'
        command = ("if command -v ufw >/dev/null 2>&1 && ufw status | grep -q 'Status: active'; then "
                   f"ufw allow from {shlex.quote(source)} to any port {agent_port} proto tcp; "
                   "else echo 'UFW inactive; confirm the host/datacenter firewall allows only the bot IP'; fi")
        return ServerMonitor._ssh_exec_sync(target_ip, port, user, password, command, timeout=25)

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

            if str(out).startswith("AUTH_FAILED"):
                return {"status": "Offline", "error": str(out)}
            return {"status": "Offline", "error": str(out), "transport": "ssh"}
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
        cmd = "apt-get autoremove -y && apt-get clean && journalctl --vacuum-time=7d"
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
    def format_full_global_results(data):
        if not isinstance(data,dict): return '❌ دادهٔ پینگ جهانی معتبر نیست.'
        lines=[]
        for node,result in data.items():
            samples=[]
            try:
                for packet in result[0] if result else []:
                    if packet and packet[0]=='OK': samples.append(float(packet[1])*1000)
            except (TypeError,ValueError,IndexError):
                pass
            latency=f'{min(samples):.0f}/{statistics.mean(samples):.0f}/{max(samples):.0f} ms' if samples else 'Timeout'
            lines.append(f'{node}: {latency}')
        return ('🌍 پینگ جهانی (حداقل/میانگین/حداکثر)\n'+'\n'.join(lines))[:3600] if lines else 'داده‌ای دریافت نشد.'

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
        os.makedirs(os.path.dirname(KEY_FILE), mode=0o700, exist_ok=True)
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
        if os.stat(KEY_FILE).st_mode & 0o777 != 0o600:
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
