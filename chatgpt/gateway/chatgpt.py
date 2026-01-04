import hashlib
import json
import random
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import HTMLResponse, Response

from app import app, templates
from gateway.login import login_html
import utils.globals as globals
from utils.kv_utils import set_value_for_key_list
from utils.Client import Client
from utils.Logger import logger
from utils.configs import chatgpt_base_url_list, proxy_url_list
from chatgpt.fp import get_fp
from chatgpt.authorization import verify_token, get_req_token

with open("templates/chatgpt_context_1.json", "r", encoding="utf-8") as f:
    chatgpt_context_1 = json.load(f)
with open("templates/chatgpt_context_2.json", "r", encoding="utf-8") as f:
    chatgpt_context_2 = json.load(f)


def is_direct_token(token: str) -> bool:
    if not token:
        return False
    token = token.strip()
    return len(token) == 45 or token.startswith("eyJhbGciOi") or token.startswith("fk-")


def get_proxy_url(request: Request):
    if not proxy_url_list:
        return None
    proxy_url = random.choice(proxy_url_list)
    if "{}" in proxy_url:
        seed = request.cookies.get("token", "") or "auth"
        session_id = hashlib.md5(seed.encode()).hexdigest()
        proxy_url = proxy_url.replace("{}", session_id)
    return proxy_url


async def fetch_access_token_from_session(request: Request):
    cookies = dict(request.cookies)
    if not cookies:
        return None
    base_url = random.choice(chatgpt_base_url_list) if chatgpt_base_url_list else "https://chatgpt.com"
    headers = {"accept": "application/json"}
    user_agent = request.headers.get("user-agent")
    if user_agent:
        headers["user-agent"] = user_agent
    proxy_url = get_proxy_url(request)
    client = Client(proxy=proxy_url)
    try:
        r = await client.get(f"{base_url}/api/auth/session", headers=headers, cookies=cookies, timeout=10)
    except Exception as exc:
        logger.error(f"Failed to fetch auth session: {exc}")
        return None
    finally:
        await client.close()
    if r.status_code != 200:
        return None
    try:
        session_info = r.json()
    except Exception:
        return None
    return session_info.get("accessToken")


def save_access_token(access_token: str):
    token = access_token.strip()
    if not token:
        return False
    if globals.token_list == [token]:
        return True
    globals.token_list.clear()
    globals.error_token_list.clear()
    globals.token_list.append(token)
    with open(globals.TOKENS_FILE, "w", encoding="utf-8") as f:
        f.write(token + "\n")
    with open(globals.ERROR_TOKENS_FILE, "w", encoding="utf-8") as f:
        pass
    logger.info("Token saved from web login.")
    return True


async def fetch_workspace_accounts(request: Request, token: str):
    base_url = random.choice(chatgpt_base_url_list) if chatgpt_base_url_list else "https://chatgpt.com"
    req_token = get_req_token(token)
    try:
        access_token = await verify_token(req_token)
    except Exception as exc:
        logger.error(f"Workspace token verify failed: {exc}")
        return None, 401
    headers = {"accept": "application/json", "authorization": f"Bearer {access_token}"}
    fp = get_fp(req_token).copy()
    proxy_url = fp.pop("proxy_url", None)
    impersonate = fp.pop("impersonate", "safari15_3")
    user_agent = fp.get("user-agent") or request.headers.get("user-agent")
    if user_agent:
        headers["user-agent"] = user_agent
    headers.update(fp)
    headers.update({
        "accept-language": "en-US,en;q=0.9",
        "host": base_url.replace("https://", "").replace("http://", ""),
        "origin": base_url,
        "referer": f"{base_url}/"
    })
    client = Client(proxy=proxy_url, impersonate=impersonate)
    try:
        r = await client.get(f"{base_url}/backend-api/accounts/check/v4-2023-04-27", headers=headers, timeout=10)
    except Exception as exc:
        logger.error(f"Failed to fetch workspaces: {exc}")
        return None, 502
    finally:
        await client.close()
    if r.status_code != 200:
        try:
            body = r.text[:200]
        except Exception:
            body = ""
        logger.error(f"Workspace fetch failed: {r.status_code} {body}")
        return None, r.status_code
    try:
        return r.json(), 200
    except Exception:
        return None, 502


@app.get("/chatgpt/workspaces")
@app.get("/workspaces")
async def get_workspaces(request: Request):
    token = request.cookies.get("token")
    if not token:
        token = await fetch_access_token_from_session(request)
    if not token and globals.token_list:
        token = globals.token_list[0]
    if not token:
        return Response(content=json.dumps({"error": "no_token"}), media_type="application/json", status_code=401)
    data, status = await fetch_workspace_accounts(request, token)
    if not data:
        return Response(content=json.dumps({"error": "fetch_failed"}), media_type="application/json", status_code=status)
    return Response(content=json.dumps(data), media_type="application/json")


@app.get("/switcher.js")
async def workspace_switcher_js(request: Request):
    script = r"""
(function () {
  if (window.__ws_switcher_loaded) return;
  window.__ws_switcher_loaded = true;

  var panelId = 'ws-panel';
  var state = { loading: false, loaded: false, list: [] };

  function getCookie(name) {
    var match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
    return match ? decodeURIComponent(match[2]) : '';
  }

  function setCookie(name, value, days) {
    var expires = '';
    if (days) {
      var date = new Date();
      date.setTime(date.getTime() + (days * 24 * 60 * 60 * 1000));
      expires = '; expires=' + date.toUTCString();
    }
    document.cookie = name + '=' + encodeURIComponent(value || '') + expires + '; path=/; SameSite=Lax';
  }

  function setSelectMessage(sel, text) {
    if (!sel) return;
    sel.innerHTML = '';
    var option = document.createElement('option');
    option.value = '';
    option.textContent = text;
    sel.appendChild(option);
  }

  function setStatus(msgEl, text, ok) {
    if (!msgEl) return;
    msgEl.textContent = text || '';
    msgEl.style.color = ok ? '#4ade80' : '#fbbf24';
    if (text) {
      setTimeout(function () { msgEl.textContent = ''; }, 2000);
    }
  }

  function buildPanel() {
    var panel = document.getElementById(panelId);
    if (panel) return panel;
    if (!document.body) return null;

    panel = document.createElement('div');
    panel.id = panelId;
    panel.style.cssText = 'position:fixed;top:12px;right:80px;z-index:2147483647;background:#1a1a2e;padding:10px 14px;border-radius:10px;box-shadow:0 4px 15px rgba(0,0,0,0.4);display:flex;align-items:center;gap:10px;font-family:system-ui;';
    panel.innerHTML = '<span style="color:#888;font-size:11px;">Workspace:</span>' +
      '<select id="ws-sel" style="background:#2a2a4a;color:#fff;border:1px solid #444;border-radius:6px;padding:5px 10px;font-size:12px;cursor:pointer;max-width:200px;"></select>' +
      '<button id="ws-btn" style="background:#4ade80;color:#000;border:none;border-radius:6px;padding:6px 14px;font-size:12px;font-weight:600;cursor:pointer;">Apply</button>' +
      '<span id="ws-msg" style="color:#4ade80;font-size:11px;min-width:60px;"></span>';
    document.body.appendChild(panel);
    return panel;
  }

  function populateOptions(sel, msg) {
    sel.innerHTML = '';
    if (!state.list.length) {
      setSelectMessage(sel, 'No workspace');
      setStatus(msg, 'No workspace', false);
      return;
    }
    state.list.forEach(function (ws) {
      var option = document.createElement('option');
      option.value = ws.id;
      option.textContent = ws.plan ? (ws.name + ' (' + ws.plan + ')') : ws.name;
      option.dataset.orgId = ws.orgId || '';
      sel.appendChild(option);
    });

    var savedId = getCookie('ws_id');
    var savedOrg = getCookie('ws_org');
    if (savedId && sel.querySelector('option[value="' + savedId + '"]')) {
      sel.value = savedId;
      if (!savedOrg) {
        savedOrg = sel.options[sel.selectedIndex].dataset.orgId || '';
        setCookie('ws_org', savedOrg, 365);
      }
    } else {
      var firstOrg = sel.options[0] ? sel.options[0].dataset.orgId : '';
      setCookie('ws_id', sel.value, 365);
      setCookie('ws_org', firstOrg, 365);
    }

    sel.disabled = false;
    setStatus(msg, 'Ready', true);
  }

  function getBasePath() {
    try {
      if (document.currentScript && document.currentScript.src) {
        var p = new URL(document.currentScript.src, location.origin).pathname;
        return p.replace(/\/switcher\.js.*$/, '/');
      }
    } catch (e) {}
    var path = location.pathname || '/';
    if (!path.endsWith('/')) path = path + '/';
    return path;
  }

  function loadWorkspaces(sel, msg) {
    if (state.loaded || state.loading) return;
    state.loading = true;
    setSelectMessage(sel, 'Loading...');
    sel.disabled = true;
    var basePath = getBasePath();
    if (basePath.indexOf('/chatgpt/') === -1) {
      basePath = '/chatgpt/';
    }
    fetch(basePath + 'workspaces', { headers: { 'accept': 'application/json' } })
      .then(function (r) {
        if (!r.ok) throw new Error('http_' + r.status);
        var contentType = r.headers.get('content-type') || '';
        if (contentType.indexOf('application/json') === -1) {
          throw new Error('not_json');
        }
        return r.json();
      })
      .then(function (data) {
        var ordering = data.account_ordering || [];
        var accounts = data.accounts || {};
        var list = [];
        ordering.forEach(function (id) {
          var entry = accounts[id] || {};
          var account = entry.account || entry;
          list.push({
            id: id,
            name: account.name || account.account_name || account.email || id,
            plan: account.plan_type || account.plan || '',
            orgId: account.organization_id || account.org_id || ''
          });
        });
        state.list = list;
        state.loaded = true;
        state.loading = false;
        populateOptions(sel, msg);
      })
      .catch(function () {
        state.loading = false;
        setSelectMessage(sel, 'Load failed');
        setStatus(msg, 'Load failed', false);
      });
  }

  function attachHeaderInterceptor() {
    if (!window.fetch) return;
    var originalFetch = window.fetch;
    window.fetch = function (input, init) {
      var url = typeof input === 'string' ? input : (input && input.url ? input.url : '');
      if (url.indexOf('/backend-api/') === -1 && url.indexOf('/public-api/') === -1 && url.indexOf('/gizmos/') === -1) {
        return originalFetch(input, init);
      }
      var headers = new Headers((init && init.headers) || (input && input.headers) || {});
      var wsId = getCookie('ws_id');
      var wsOrg = getCookie('ws_org');
      if (wsId) headers.set('openai-organization', wsId);
      if (wsOrg) headers.set('oai-organization-id', wsOrg);
      init = init || {};
      init.headers = headers;
      if (input instanceof Request) {
        input = new Request(input, init);
        return originalFetch(input);
      }
      return originalFetch(input, init);
    };
  }

  function init() {
    var panel = buildPanel();
    if (!panel) return;
    var sel = panel.querySelector('#ws-sel');
    var btn = panel.querySelector('#ws-btn');
    var msg = panel.querySelector('#ws-msg');
    if (!sel || sel.dataset.bound === '1') return;
    sel.dataset.bound = '1';
    btn.onclick = function () {
      var orgId = sel.options[sel.selectedIndex] ? (sel.options[sel.selectedIndex].dataset.orgId || '') : '';
      setCookie('ws_id', sel.value, 365);
      setCookie('ws_org', orgId, 365);
      setStatus(msg, 'Applied', true);
      location.reload();
    };
    sel.onchange = function () {
      setStatus(msg, 'Pending', false);
    };
    loadWorkspaces(sel, msg);
    attachHeaderInterceptor();
  }

  init();
  setInterval(init, 1000);
})();
"""
    return Response(content=script, media_type="application/javascript")



@app.get("/", response_class=HTMLResponse)
async def chatgpt_html(request: Request):
    token = request.query_params.get("token")
    if token:
        token = token.strip()
        if not token:
            token = None
        elif is_direct_token(token):
            save_access_token(token)
    if not token:
        token = request.cookies.get("token")
        if token:
            token = token.strip()
            if token and is_direct_token(token):
                save_access_token(token)
    if not token:
        access_token = await fetch_access_token_from_session(request)
        if access_token:
            if is_direct_token(access_token):
                save_access_token(access_token)
            token = access_token
        else:
            return await login_html(request)

    if len(token) != 45 and not token.startswith("eyJhbGciOi"):
        token = quote(token)

    user_chatgpt_context_1 = chatgpt_context_1.copy()
    user_chatgpt_context_2 = chatgpt_context_2.copy()

    set_value_for_key_list(user_chatgpt_context_1, "accessToken", token)
    if request.cookies.get("oai-locale"):
        set_value_for_key_list(user_chatgpt_context_1, "locale", request.cookies.get("oai-locale"))
    else:
        accept_language = request.headers.get("accept-language")
        if accept_language:
            set_value_for_key_list(user_chatgpt_context_1, "locale", accept_language.split(",")[0])

    user_chatgpt_context_1 = json.dumps(user_chatgpt_context_1, separators=(',', ':'), ensure_ascii=False)
    user_chatgpt_context_2 = json.dumps(user_chatgpt_context_2, separators=(',', ':'), ensure_ascii=False)

    escaped_context_1 = user_chatgpt_context_1.replace("\\", "\\\\").replace('"', '\\"')
    escaped_context_2 = user_chatgpt_context_2.replace("\\", "\\\\").replace('"', '\\"')

    clear_localstorage_script = ""
    if request.query_params.get("clear_storage") == "1":
        clear_localstorage_script = """
    <script>
        localStorage.clear();
    </script>
    """

    workspace_switcher_script = ""
    try:
        ws_data, status = await fetch_workspace_accounts(request, token)
        if ws_data and ws_data.get("account_ordering"):
            accounts = ws_data.get("accounts", {})
            ordering = ws_data.get("account_ordering", [])
            ws_list = []
            for account_id in ordering:
                entry = accounts.get(account_id, {})
                account = entry.get("account", entry)
                ws_list.append({
                    "id": account_id,
                    "name": account.get("name") or account.get("account_name") or account.get("email") or account_id,
                    "plan": account.get("plan_type") or account.get("plan") or "",
                    "orgId": account.get("organization_id") or account.get("org_id") or ""
                })
            ws_json = json.dumps(ws_list, ensure_ascii=True)
            workspace_switcher_script = f"""
    <script nonce="20f285f6-3f39-465d-8802-567f078c6c55">
    (function() {{
        var workspaces = {ws_json};
        if (!workspaces || !workspaces.length) return;
        var panelId = "ws-panel";

        function getCookie(name) {{
            var match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
            return match ? decodeURIComponent(match[2]) : '';
        }}

        function setCookie(name, value, days) {{
            var expires = '';
            if (days) {{
                var date = new Date();
                date.setTime(date.getTime() + (days * 24 * 60 * 60 * 1000));
                expires = '; expires=' + date.toUTCString();
            }}
            document.cookie = name + '=' + encodeURIComponent(value || '') + expires + '; path=/; SameSite=Lax';
        }}

        function buildPanel() {{
            var panel = document.getElementById(panelId);
            if (panel) return panel;
            if (!document.body) return null;
            panel = document.createElement('div');
            panel.id = panelId;
            panel.style.cssText = 'position:fixed;top:12px;right:80px;z-index:2147483647;background:#1a1a2e;padding:10px 14px;border-radius:10px;box-shadow:0 4px 15px rgba(0,0,0,0.4);display:flex;align-items:center;gap:10px;font-family:system-ui;';
            panel.innerHTML = '<span style="color:#888;font-size:11px;">Workspace:</span>' +
                '<select id="ws-sel" style="background:#2a2a4a;color:#fff;border:1px solid #444;border-radius:6px;padding:5px 10px;font-size:12px;cursor:pointer;max-width:200px;"></select>' +
                '<button id="ws-btn" style="background:#4ade80;color:#000;border:none;border-radius:6px;padding:6px 14px;font-size:12px;font-weight:600;cursor:pointer;">Apply</button>' +
                '<span id="ws-msg" style="color:#4ade80;font-size:11px;min-width:60px;"></span>';
            document.body.appendChild(panel);
            return panel;
        }}

        function populateOptions(sel, msg) {{
            sel.innerHTML = '';
            workspaces.forEach(function(ws) {{
                var option = document.createElement('option');
                option.value = ws.id;
                option.textContent = ws.plan ? (ws.name + ' (' + ws.plan + ')') : ws.name;
                option.dataset.orgId = ws.orgId || '';
                sel.appendChild(option);
            }});
            var savedId = getCookie('ws_id');
            var savedOrg = getCookie('ws_org');
            if (savedId && sel.querySelector('option[value="' + savedId + '"]')) {{
                sel.value = savedId;
                if (!savedOrg) {{
                    savedOrg = sel.options[sel.selectedIndex].dataset.orgId || '';
                    setCookie('ws_org', savedOrg, 365);
                }}
            }} else {{
                var firstOrg = sel.options[0] ? sel.options[0].dataset.orgId : '';
                setCookie('ws_id', sel.value, 365);
                setCookie('ws_org', firstOrg, 365);
            }}
            sel.disabled = false;
            if (msg) {{
                msg.textContent = 'Ready';
                msg.style.color = '#4ade80';
                setTimeout(function() {{ msg.textContent = ''; }}, 2000);
            }}
        }}

        function init() {{
            var panel = buildPanel();
            if (!panel) return;
            var sel = panel.querySelector('#ws-sel');
            var btn = panel.querySelector('#ws-btn');
            var msg = panel.querySelector('#ws-msg');
            if (!sel || sel.dataset.bound === '1') return;
            sel.dataset.bound = '1';
            sel.disabled = true;
            btn.onclick = function() {{
                var orgId = sel.options[sel.selectedIndex] ? (sel.options[sel.selectedIndex].dataset.orgId || '') : '';
                setCookie('ws_id', sel.value, 365);
                setCookie('ws_org', orgId, 365);
                if (msg) {{
                    msg.textContent = 'Applied';
                    msg.style.color = '#4ade80';
                    setTimeout(function() {{ msg.textContent = ''; }}, 2000);
                }}
                location.reload();
            }};
            sel.onchange = function() {{
                if (msg) {{
                    msg.textContent = 'Pending';
                    msg.style.color = '#fbbf24';
                }}
            }};
            populateOptions(sel, msg);
        }}

        init();
        setInterval(init, 1000);
    }})();
    </script>
            """
    except Exception as exc:
        logger.error(f"Workspace switcher render failed: {exc}")

    response = templates.TemplateResponse("chatgpt.html", {
        "request": request,
        "react_chatgpt_context_1": escaped_context_1,
        "react_chatgpt_context_2": escaped_context_2,
        "clear_localstorage_script": clear_localstorage_script,
        "workspace_switcher_script": workspace_switcher_script
    })
    response.set_cookie("token", value=token, expires="Thu, 01 Jan 2099 00:00:00 GMT")
    return response
