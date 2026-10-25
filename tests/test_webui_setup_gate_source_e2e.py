from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

import pytest

from test_webui_spa_frontend import _cdp_frame, _cdp_recv, _read_exact
from thyca.config import Config, ProviderEntry, save

ROOT = Path(__file__).resolve().parents[1]


class LocalModelStub(BaseHTTPRequestHandler):
    requests = 0
    post_requests = 0
    post_paths: list[str] = []
    auth_present = False
    auth_matched = False
    expected_key = "synthetic-e2e-only-key"

    def log_message(self, *_args) -> None:
        pass

    def do_GET(self) -> None:
        if self.path != "/v1/models":
            self.send_error(404)
            return
        type(self).requests += 1
        authorization = self.headers.get("Authorization", "")
        type(self).auth_present = bool(authorization)
        type(self).auth_matched = authorization == f"Bearer {type(self).expected_key}"
        body = json.dumps({"data": [{"id": "fixture-model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        type(self).post_requests += 1
        type(self).post_paths.append(self.path)
        body = json.dumps({"error": "E2E stub forbids model completion calls"}).encode()
        self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_source_cli(root: Path, port: int, env: dict[str, str], log_path: Path):
    source = "from pathlib import Path; import sys; from thyca.app.cli import Cli; raise SystemExit(Cli(thyca_dir=Path(sys.argv[1])).main(['--serve', '--port', sys.argv[2]]))"
    log = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-c", source, str(root), str(port)],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    return process, log


def _wait_server(process: subprocess.Popen, port: int, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"source CLI exited early with {process.returncode}")
        try:
            with urlopen(f"http://127.0.0.1:{port}/api/config/status", timeout=0.5) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"source CLI did not start on port {port}")


def _stop_source_cli(process: subprocess.Popen, log, port: int, proof: list[dict]) -> None:
    forced = False
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            forced = True
            process.kill()
            process.wait(timeout=5)
    log.close()
    proof.append({"pid": process.pid, "port": port, "exitCode": process.returncode, "forced": forced, "stopped": process.poll() is not None})


def _browser_e2e(chrome: str, url: str, user_data: Path, script: str, screenshot: Path | None, *, width: int = 1280, height: int = 900, mobile: bool = False) -> dict:
    debug_port = _free_port()
    process = subprocess.Popen(
        [chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
         "--no-first-run", "--no-default-browser-check", "--no-proxy-server",
         f"--remote-debugging-port={debug_port}", f"--user-data-dir={user_data}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=dict(os.environ, HOME="/tmp/sandbox-home"),
    )
    sock = None
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                with urlopen(f"http://127.0.0.1:{debug_port}/json/version", timeout=0.5):
                    break
            except OSError:
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("Chrome DevTools did not start")
                time.sleep(0.05)
        request = Request(f"http://127.0.0.1:{debug_port}/json/new?{quote('about:blank', safe=':')}", method="PUT")
        with urlopen(request, timeout=3) as response:
            target = json.loads(response.read())
        ws = urlsplit(target["webSocketDebuggerUrl"])
        sock = socket.create_connection((ws.hostname, ws.port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall((f"GET {ws.path} HTTP/1.1\r\nHost: {ws.netloc}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        header = bytearray()
        while not header.endswith(b"\r\n\r\n"):
            header.extend(sock.recv(1))
        if b" 101 " not in header:
            raise ConnectionError(header.decode(errors="replace"))
        sequence = 0

        def call(method: str, params: dict | None = None) -> dict:
            nonlocal sequence
            sequence += 1
            command_id = sequence
            _cdp_frame(sock, json.dumps({"id": command_id, "method": method, "params": params or {}}).encode())
            while True:
                response = _cdp_recv(sock)
                if response.get("id") != command_id:
                    continue
                if "error" in response:
                    raise RuntimeError(response["error"])
                return response.get("result", {})

        call("Page.enable")
        call("Runtime.enable")
        call("Emulation.setDeviceMetricsOverride", {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": mobile})
        call("Page.addScriptToEvaluateOnNewDocument", {"source": script})
        call("Page.navigate", {"url": url})
        end = time.monotonic() + 35
        while time.monotonic() < end:
            encoded = call("Runtime.evaluate", {"expression": "document.body?.dataset.sourceE2eResult || ''", "returnByValue": True}).get("result", {}).get("value")
            if encoded:
                result = json.loads(encoded)
                if screenshot is not None:
                    screenshot_data = call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
                    screenshot.write_bytes(base64.b64decode(screenshot_data["data"]))
                return result
            time.sleep(0.05)
        state = call("Runtime.evaluate", {"expression": "JSON.stringify({route:window.ThycaRouter?.currentRoute(),setup:window.ThycaRouter?.setupState(),errors:window.__sourceErrors||[],result:window.__sourceResult,body:document.body?.dataset,apiCalls:window.__sourceApiCalls,detail:document.querySelector('#detail-body')?.textContent})", "returnByValue": True}).get("result", {}).get("value", "{}")
        raise TimeoutError(f"source E2E browser did not finish: {state}")
    finally:
        if sock is not None:
            sock.close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


E2E_SCRIPT = r"""
(() => {
  window.__sourceErrors = [];
  window.__sourceApiRequests = [];
  const sourceFetch = window.fetch.bind(window);
  window.fetch = (input, init = {}) => {
    try { const url = new URL(typeof input === 'string' ? input : input.url, location.href); if (url.origin === location.origin && url.pathname.startsWith('/api/')) window.__sourceApiRequests.push({path:url.pathname,method:(init.method || input.method || 'GET').toUpperCase()}); } catch {}
    return sourceFetch(input, init);
  };
  window.addEventListener('error', event => window.__sourceErrors.push(String(event.message)));
  window.addEventListener('unhandledrejection', event => window.__sourceErrors.push(String(event.reason)));
  const wait = async predicate => { const until=Date.now()+18000; while(Date.now()<until){if(predicate())return;await new Promise(resolve=>setTimeout(resolve,25));} throw Error('timeout '+JSON.stringify({route:window.ThycaRouter?.currentRoute(),setup:window.ThycaRouter?.setupState(),errors:window.__sourceErrors})); };
  const finish = value => { value.browserErrors=window.__sourceErrors; document.body.dataset.sourceE2eResult=JSON.stringify(value); };
  (async()=>{try{
    const mode=new URLSearchParams(location.search).get('e2eMode');
    if(mode==='env-ready'){
      await wait(()=>window.ThycaRouter?.setupState()==='ready' && window.ThycaRouter.currentPage()==='profile' && !document.querySelector('[data-page-workspace="profile"]').hidden);
      finish({passed:true,setup:window.ThycaRouter.setupState(),route:window.ThycaRouter.currentRoute(),notice:!!document.querySelector('[data-setup-notice]'),localStorageKeys:Object.keys(localStorage)}); return;
    }
    await wait(()=>window.ThycaRouter?.setupState()==='required' && window.ThycaRouter.currentPage()==='provider' && !document.querySelector('[data-page-workspace="provider"]').hidden);
    const beforeSessions=await fetch('/api/sessions').then(response=>response.json());
    const before={route:window.ThycaRouter.currentRoute(),setup:window.ThycaRouter.setupState(),chatHidden:document.querySelector('#chat-workspace').hidden,chatInert:document.querySelector('#chat-workspace').inert,sessions:beforeSessions.sessions.length,dmLookups:window.__sourceApiRequests.filter(request=>request.path==='/api/bots/thyca/dm').length,historyPage:history.state?.thycaSpa?.page};
    await window.ThycaRouter.navigate('chat');
    await wait(()=>window.ThycaRouter.currentPage()==='provider');
    document.querySelector('#new-connection').click();
    const values={url:__STUB_URL__,key:'synthetic-e2e-only-key'};
    for(const [selector,value] of [['#pv-base-url',values.url],['#pv-key',values.key]]){const field=document.querySelector(selector);field.value=value;field.dispatchEvent(new Event('input',{bubbles:true}));}
    document.querySelector('#v-submit').click();
    await wait(()=>[...document.querySelector('#m-picker').options].some(option=>option.dataset.model==='fixture-model'));
    const picker=document.querySelector('#m-picker');picker.value='model:fixture-model';picker.dispatchEvent(new Event('change',{bubbles:true}));
    const name=document.querySelector('#pv-id');name.value='local';name.dispatchEvent(new Event('input',{bubbles:true}));
    document.querySelector('#save-use').click();
    await wait(()=>window.ThycaRouter.setupState()==='ready' && window.ThycaRouter.currentRoute().page==='chat' && !document.querySelector('#chat-workspace').hidden);
    await wait(()=>[...document.querySelector('#composer-model').options].some(option=>option.value==='fixture-model') && document.querySelector('#composer-model').options[0].textContent.includes('fixture-model · local'));
    const status=await fetch('/api/config/status').then(response=>response.json());
    const config=await fetch('/api/config').then(response=>response.json());
    const settingsResponse=await fetch('/api/bots/thyca/settings');
    const settings=await settingsResponse.json();
    const sessions=await fetch('/api/sessions').then(response=>response.json());
    const chatDmLookups=window.__sourceApiRequests.filter(request=>request.path==='/api/bots/thyca/dm'&&request.method==='GET').length;
    const dmDetail=await fetch('/api/bots/thyca/dm').then(response=>response.json());
    const provider=config.values.providers.local,model=config.values.models['fixture-model'];
    const modelOption=[...document.querySelector('#composer-model').options].find(option=>option.value==='fixture-model');
    const postPaths=window.__sourceApiRequests.filter(request=>request.method==='POST').map(request=>request.path);
    finish({passed:true,before,route:window.ThycaRouter.currentRoute(),setup:window.ThycaRouter.setupState(),notice:!!document.querySelector('[data-setup-notice]'),chatVisible:!document.querySelector('#chat-workspace').hidden&&!document.querySelector('#chat-workspace').inert,
      serverReady:status.ready,defaultModel:config.values.defaultModel,defaultProvider:config.values.defaultProvider,modelProvider:model?.provider,
      providerBaseUrl:provider?.baseUrl,providerKeyMasked:provider?.apiKey==='',providerKeyPresent:config.meta?.providers?.local===true,
      settingsHttpOk:settingsResponse.ok,settingsBot:settings.bot_id,settingsEffectiveModel:settings.effective?.model,settingsEffectiveProvider:settings.effective?.provider,
      composerInheritLabel:document.querySelector('#composer-model').options[0].textContent,composerModelOption:modelOption?.value,
      sessionsAfter:sessions.sessions.length,sessionDm:dmDetail.dm,dmKind:dmDetail.kind,dmSessionId:dmDetail.id,chatDmLookups,postPaths,turnPostCount:postPaths.filter(path=>path.includes('/turn')).length,localStorageKeys:Object.keys(localStorage)});
  }catch(error){finish({error:String(error),stack:error.stack,route:window.ThycaRouter?.currentRoute(),setup:window.ThycaRouter?.setupState(),fields:{url:document.querySelector('#pv-base-url')?.value,connection:document.querySelector('#pv-id')?.value,keyPresent:!!document.querySelector('#pv-key')?.value,discovery:document.querySelector('#discovery-status')?.textContent,formStatus:document.querySelector('#editor-status')?.textContent,verifyDisabled:document.querySelector('#v-submit')?.disabled}});}})();
})();
"""


@pytest.mark.skipif(not shutil.which("google-chrome"), reason="Google Chrome is required for the actual-source onboarding E2E")
def test_source_cli_first_run_gate_and_environment_key_readiness(tmp_path: Path) -> None:
    LocalModelStub.requests = 0
    LocalModelStub.auth_present = False
    LocalModelStub.auth_matched = False
    LocalModelStub.post_requests = 0
    LocalModelStub.post_paths = []
    stub = ThreadingHTTPServer(("127.0.0.1", 0), LocalModelStub)
    stub_thread = threading.Thread(target=stub.serve_forever, daemon=True)
    stub_thread.start()
    stub_port = stub.server_port

    source_env = dict(os.environ, HOME="/tmp/sandbox-home", PYTHONPATH=str(ROOT))
    source_env.pop("THYCA_SETUP_GATE_E2E_KEY", None)
    source_root = tmp_path / "first-run-data"
    source_root.mkdir()
    save(Config(
        providers={"legacy": ProviderEntry(baseUrl=f"http://127.0.0.1:{stub_port}/v1", apiKeyEnv="THYCA_SETUP_GATE_E2E_KEY")},
        defaultProvider="legacy", defaultModel="legacy/unregistered", models={},
    ), source_root / "config.json")
    source_port = _free_port()
    server_log = Path("/tmp/thyca-setup-gate-source-e2e-server.log")
    process, log = _start_source_cli(source_root, source_port, source_env, server_log)
    stops: list[dict] = []
    screenshot = Path("/tmp/thyca-setup-gate-source-e2e.png")
    evidence_path = Path("/tmp/thyca-provider-single-editor-source-e2e.json")
    try:
        _wait_server(process, source_port)
        with urlopen(f"http://127.0.0.1:{source_port}/api/config/status", timeout=3) as response:
            initial_status = json.loads(response.read())
        with urlopen(f"http://127.0.0.1:{source_port}/api/config", timeout=3) as response:
            initial_config = json.loads(response.read())
        initial_session_files = list((source_root / "sessions").glob("*.jsonl"))
        assert not initial_session_files
        chrome = shutil.which("google-chrome")
        page_url = f"http://127.0.0.1:{source_port}/?e2eMode=first-run"
        browser_script = E2E_SCRIPT.replace("__STUB_URL__", json.dumps(f"http://127.0.0.1:{stub_port}/v1"))
        browser = _browser_e2e(chrome, page_url, tmp_path / "chrome-first-run", browser_script, screenshot)
        assert browser.get("passed") is True, browser
        assert browser["before"]["setup"] == "required" and browser["before"]["route"]["page"] == "provider"
        assert browser["before"]["chatHidden"] and browser["before"]["chatInert"] and browser["before"]["sessions"] == 0 and browser["before"]["dmLookups"] == 0
        assert browser["setup"] == "ready" and browser["route"] == {"page": "chat", "search": "?bot=thyca", "hash": ""} and browser["chatVisible"]
        assert browser["serverReady"] is True and browser["defaultModel"] == "fixture-model" and browser["defaultProvider"] == "legacy"
        assert browser["settingsEffectiveModel"] == "fixture-model" and browser["settingsEffectiveProvider"] == "local"
        assert browser["composerInheritLabel"] == "Theo cấu hình bot (fixture-model · local)" and browser["composerModelOption"] == "fixture-model"
        assert browser["sessionsAfter"] == 0 and browser["sessionDm"] == "thyca" and browser["dmKind"] == "dm" and browser["dmSessionId"]
        assert browser["chatDmLookups"] == 1
        session_files = list((source_root / "sessions").glob("*.jsonl"))
        assert len(session_files) == 1
        assert browser["postPaths"] == ["/api/onboarding/verify", "/api/config"] and browser["turnPostCount"] == 0
        assert browser["modelProvider"] == "local" and browser["providerKeyMasked"] and browser["providerKeyPresent"]
        assert browser["settingsHttpOk"] and browser["settingsBot"] == "thyca" and browser["settingsEffectiveProvider"] == "local"
        assert browser["browserErrors"] == []
        assert LocalModelStub.requests == 1 and LocalModelStub.auth_present and LocalModelStub.auth_matched
        assert LocalModelStub.post_requests == 0 and LocalModelStub.post_paths == []
        saved_config = json.loads((source_root / "config.json").read_text(encoding="utf-8"))
        assert saved_config["defaultModel"] == "fixture-model" and saved_config["models"]["fixture-model"]["provider"] == "local"
        assert "apiKey" not in saved_config["providers"]["local"]
        assert "synthetic-e2e-only-key" not in json.dumps(browser, ensure_ascii=False)

        _stop_source_cli(process, log, source_port, stops)
        env_root = tmp_path / "environment-key-data"
        env_root.mkdir()
        save(Config(
            providers={"legacy": ProviderEntry(baseUrl=f"http://127.0.0.1:{stub_port}/v1", apiKeyEnv="THYCA_SETUP_GATE_ENV_KEY")},
            defaultProvider="legacy", defaultModel="legacy/unregistered", models={},
        ), env_root / "config.json")
        env_port = _free_port()
        env = dict(source_env, THYCA_SETUP_GATE_ENV_KEY="synthetic-env-only-key")
        env_log_path = Path("/tmp/thyca-setup-gate-env-e2e-server.log")
        env_process, env_log = _start_source_cli(env_root, env_port, env, env_log_path)
        try:
            _wait_server(env_process, env_port)
            with urlopen(f"http://127.0.0.1:{env_port}/api/config/status", timeout=3) as response:
                env_status = json.loads(response.read())
            with urlopen(f"http://127.0.0.1:{env_port}/api/config", timeout=3) as response:
                env_config = json.loads(response.read())
            env_page = f"http://127.0.0.1:{env_port}/ui/profile?bot=thyca&e2eMode=env-ready"
            env_browser = _browser_e2e(chrome, env_page, tmp_path / "chrome-env-ready", E2E_SCRIPT, None)
            assert env_status["ready"] is True and not env_config["values"].get("models")
            assert env_browser.get("passed") is True and env_browser["setup"] == "ready" and env_browser["notice"] is False
            assert env_browser["route"] == {"page": "profile", "search": "?bot=thyca", "hash": ""}
            assert env_browser["browserErrors"] == [] and "synthetic-env-only-key" not in json.dumps(env_browser)
            proof = {"firstRun": {"initialStatusReady": initial_status["ready"], "initialDefaultModelUnregistered": initial_config["values"]["defaultModel"], "browser": browser,
                                  "chatRestoration": {"route": browser["route"], "chatVisible": browser["chatVisible"], "sessionsBefore": browser["before"]["sessions"], "sessionsAfter": browser["sessionsAfter"], "sessionFilesBefore": len(initial_session_files), "sessionFilesAfter": len(session_files), "sessionDm": browser["sessionDm"], "dmKind": browser["dmKind"], "dmSessionId": browser["dmSessionId"], "chatDmLookups": browser["chatDmLookups"], "defaultModel": browser["defaultModel"], "defaultProvider": browser["defaultProvider"], "effectiveModel": browser["settingsEffectiveModel"], "effectiveProvider": browser["settingsEffectiveProvider"], "modelProvider": browser["modelProvider"], "composerInheritLabel": browser["composerInheritLabel"], "composerModelOption": browser["composerModelOption"], "postPaths": browser["postPaths"], "turnPostCount": browser["turnPostCount"]},
                                  "stub": {"modelListRequests": LocalModelStub.requests, "postRequests": LocalModelStub.post_requests, "postPaths": LocalModelStub.post_paths, "authorizationPresent": LocalModelStub.auth_present, "authorizationMatched": LocalModelStub.auth_matched},
                                  "savedConfigHasApiKey": "apiKey" in saved_config["providers"]["local"]},
                     "environmentKeyLegacyConfig": {"statusReady": env_status["ready"], "registeredModels": list(env_config["values"].get("models", {})), "browser": env_browser},
                     "artifacts": {"screenshot": str(screenshot), "serverLog": str(server_log), "environmentServerLog": str(env_log_path)}}
            evidence_path.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        finally:
            _stop_source_cli(env_process, env_log, env_port, stops)
    finally:
        if process.poll() is None:
            _stop_source_cli(process, log, source_port, stops)
        stub.shutdown(); stub.server_close(); stub_thread.join(timeout=3)
        Path("/tmp/thyca-setup-gate-source-e2e-stop.log").write_text(json.dumps({"cliProcesses": stops, "stubPort": stub_port, "stubStopped": not stub_thread.is_alive()}, indent=2) + "\n", encoding="utf-8")
        shutil.rmtree(tmp_path, ignore_errors=True)
