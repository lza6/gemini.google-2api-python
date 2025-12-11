import sys
import sqlite3
import random
import time
import webbrowser
import uvicorn
import asyncio
import json
import requests
from typing import AsyncGenerator

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic_settings import BaseSettings
from loguru import logger
from contextlib import asynccontextmanager

# 引入 gemini_webapi 逆向库
from gemini_webapi import GeminiClient
# 引入 HAR 解析模块
from har_parser import parse_and_validate

# --- 1. 配置加载 ---
class Settings(BaseSettings):
    model_config = {
        "env_file": ".env",
        "env_file_encoding": 'utf-8',
        "extra": "ignore"
    }
    PORT: int = 8090
    PROXY_URL: str = "http://127.0.0.1:7890"  # 记得改成你的代理端口
    GEMINI_1PSID: str = "" 
    GEMINI_1PSIDTS: str = "" 

settings = Settings()

# --- 2. 数据库逻辑 ---
DB_FILE = "data.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS accounts
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  name TEXT,
                  cookie_1psid TEXT,
                  cookie_1psidts TEXT,
                  cookie_1psidcc TEXT,
                  cookie_json TEXT,  
                  is_active INTEGER DEFAULT 1,
                  total_calls INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS logs
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  timestamp TEXT,
                  account_name TEXT,
                  model TEXT,
                  status TEXT,
                  duration INTEGER)''')
    conn.commit()
    conn.close()

def get_db_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

# --- 3. 调试函数 ---
async def debug_google_connection(name: str, cookies: dict, proxy_url: str = None):
    """调试 Google 连接问题，返回详细的诊断信息"""
    debug_info = {
        "ip_info": None,
        "gemini_response": None,
        "error": None
    }
    
    # 1. 检查代理 IP 归属
    try:
        proxies = None
        if proxy_url:
            proxies = {
                "http": proxy_url,
                "https": proxy_url
            }
        
        ip_response = requests.get("http://ip-api.com/json", proxies=proxies, timeout=10)
        ip_info = ip_response.json()
        debug_info["ip_info"] = ip_info
        
        logger.info(f"🔍 调试 [{name}] - IP: {ip_info.get('query')} | 地区: {ip_info.get('countryCode')} ({ip_info.get('country')})")
        
        if ip_info.get('countryCode') in ['CN', 'HK']:
            logger.warning(f"⚠️ 警告: 香港/中国节点极易导致 Google 验证失败！")
    except Exception as e:
        logger.error(f"🔍 调试 [{name}] - 代理连接失败: {e}")
        debug_info["error"] = f"代理连接失败: {e}"
        return debug_info
    
    # 2. 尝试访问 Gemini 首页
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        }
        
        resp = requests.get(
            "https://gemini.google.com/app",
            cookies=cookies,
            headers=headers,
            proxies=proxies,
            timeout=20,
            allow_redirects=False
        )
        
        debug_info["gemini_response"] = {
            "status_code": resp.status_code,
            "headers": dict(resp.headers),
            "has_snlM0e": "SNlM0e" in resp.text,
            "content_length": len(resp.text)
        }
        
        logger.info(f"🔍 调试 [{name}] - Gemini 响应状态: {resp.status_code}")
        
        if resp.status_code == 302:
            location = resp.headers.get('Location', '未知')
            logger.error(f"🔍 调试 [{name}] - 被重定向到: {location}")
            debug_info["error"] = f"Cookie 失效，被重定向到: {location}"
            
        elif resp.status_code == 200:
            if "SNlM0e" in resp.text:
                logger.success(f"🔍 调试 [{name}] - 找到 SNlM0e Token，连接正常！")
            else:
                # 提取页面标题
                title = "无标题"
                if '<title>' in resp.text:
                    try:
                        title = resp.text.split('<title>')[1].split('</title>')[0]
                    except:
                        pass
                logger.warning(f"🔍 调试 [{name}] - 状态码 200 但未找到 Token，页面标题: {title}")
                debug_info["error"] = f"未找到 SNlM0e Token，可能返回了验证页面: {title}"
        else:
            logger.error(f"🔍 调试 [{name}] - 异常状态码: {resp.status_code}")
            debug_info["error"] = f"异常状态码: {resp.status_code}"
            
    except Exception as e:
        logger.error(f"🔍 调试 [{name}] - 请求错误: {e}")
        debug_info["error"] = f"请求错误: {e}"
    
    return debug_info

# --- 4. 辅助函数：流式生成器 ---
async def pseudo_stream_generator(text: str, model: str) -> AsyncGenerator[bytes, None]:
    chunk_id = f"chatcmpl-{int(time.time())}"
    created = int(time.time())
    
    step = 4 
    for i in range(0, len(text), step):
        chunk_text = text[i:i+step]
        data = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {"content": chunk_text}, "finish_reason": None}]
        }
        yield f"data: {json.dumps(data)}\n\n".encode('utf-8')
        await asyncio.sleep(0.02)

    final_data = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
    }
    yield f"data: {json.dumps(final_data)}\n\n".encode('utf-8')
    yield b"data: [DONE]\n\n"

# --- 4. 核心处理逻辑 ---
async def process_gemini_request(request_data: dict, is_stream: bool):
    messages = request_data.get("messages", [])
    if not messages:
        raise ValueError("请求缺少messages参数")
    
    model = request_data.get("model", "gemini-2.5-pro")
    if model not in ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-pro"]:
        logger.warning(f"未知模型: {model}，自动回退到 gemini-2.5-pro")
        model = "gemini-2.5-pro"
    
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM accounts WHERE is_active = 1")
    accounts = cursor.fetchall()
    
    if not accounts:
        conn.close()
        raise Exception("❌ 错误：没有启用的账号，请在面板添加！")

    account = random.choice(accounts)
    acc_id = account["id"]
    acc_name = account["name"]
    
    # 优先从 JSON 读取全量 Cookie
    all_cookies = {}
    if "cookie_json" in account.keys() and account["cookie_json"]:
        try:
            all_cookies = json.loads(account["cookie_json"])
        except:
            pass
    
    # 回退机制
    if not all_cookies:
        all_cookies = {
            "__Secure-1PSID": account["cookie_1psid"],
            "__Secure-1PSIDTS": account["cookie_1psidts"]
        }
        if "cookie_1psidcc" in account.keys() and account["cookie_1psidcc"]:
             all_cookies["__Secure-1PSIDCC"] = account["cookie_1psidcc"]

    start_time = time.time()
    status = "ERROR"
    
    try:
        logger.info(f"🔄 账号 [{acc_name}] 正在处理请求...")
        
        # 初始化客户端
        client = GeminiClient(
            proxy=settings.PROXY_URL
        )
        
        # 注入全量 Cookie (包含 17 个)
        client.cookies.update(all_cookies)
        
        # 启动初始化 (带超时保护)
        await client.init(timeout=60, auto_close=True)
        
        full_prompt = ""
        for m in messages:
            if m['role'] == 'user':
                full_prompt += f"User: {m['content']}\n"
            elif m['role'] == 'assistant':
                full_prompt += f"Model: {m['content']}\n"
            elif m['role'] == 'system':
                full_prompt += f"System: {m['content']}\n"
        
        response = await client.generate_content(full_prompt, model=model)
        text = response.text
        
        status = "SUCCESS"
        cursor.execute("UPDATE accounts SET total_calls = total_calls + 1 WHERE id = ?", (acc_id,))
        conn.commit()
        
        if is_stream:
            return StreamingResponse(pseudo_stream_generator(text, model), media_type="text/event-stream")
        else:
            return JSONResponse({
                "id": f"chatcmpl-{int(time.time())}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]
            })

    except Exception as e:
        logger.error(f"❌ 账号 [{acc_name}] 调用失败: {e}")
        
        # 🔍 如果是 Cookie 相关错误，执行详细调试
        if "Cookies invalid" in str(e) or "location" in str(e).lower():
            logger.warning(f"🔍 检测到 Cookie/IP 问题，对账号 [{acc_name}] 进行诊断...")
            debug_cookies = {
                "__Secure-1PSID": cookie_1psid,
                "__Secure-1PSIDTS": cookie_1psidts
            }
            if cookie_1psidcc:
                debug_cookies["__Secure-1PSIDCC"] = cookie_1psidcc
                
            # 注意：这里是同步调用，因为 debug_google_connection 是异步的
            # 在 except 块中我们不能使用 await，所以创建一个同步版本
            try:
                loop = asyncio.get_event_loop()
                debug_result = loop.run_until_complete(debug_google_connection(acc_name, debug_cookies, settings.PROXY_URL))
                
                # 记录调试结果到日志
                if debug_result["ip_info"]:
                    ip_info = debug_result["ip_info"]
                    logger.info(f"🔍 [{acc_name}] IP: {ip_info.get('query')} ({ip_info.get('countryCode', '未知')})")
                
                if debug_result["error"]:
                    logger.error(f"🔍 [{acc_name}] 诊断结果: {debug_result['error']}")
            except Exception as debug_err:
                logger.error(f"🔍 [{acc_name}] 诊断失败: {debug_err}")
        
        if "Cookies invalid" in str(e):
             logger.critical(f"⚠️ 账号 [{acc_name}] Cookie 已失效或 IP 被拒，请更新！")
        raise e
    
    finally:
        duration = int((time.time() - start_time) * 1000)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("INSERT INTO logs (timestamp, account_name, model, status, duration) VALUES (?, ?, ?, ?, ?)",
                       (timestamp, acc_name, model, status, duration))
        conn.commit()
        conn.close()

# --- 5. FastAPI 应用初始化 ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    url = f"http://127.0.0.1:{settings.PORT}"
    logger.success(f"🚀 服务启动成功！")
    logger.info(f"👉 管理面板: {url}")
    logger.info(f"👉 API 地址: {url}/v1/chat/completions")
    webbrowser.open(url)
    yield

app = FastAPI(lifespan=lifespan, title="Gemini Desktop API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")

# === 路由部分 ===

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    conn = get_db_conn()
    accounts = conn.execute("SELECT * FROM accounts").fetchall()
    logs = conn.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 20").fetchall()
    conn.close()
    return templates.TemplateResponse("dashboard.html", {
        "request": request, 
        "accounts": accounts, 
        "logs": logs, 
        "proxy": settings.PROXY_URL,
        "api_url": f"http://127.0.0.1:{settings.PORT}"
    })

@app.post("/account/add")
async def add_account(name: str = Form(...), p1: str = Form(...), p2: str = Form(...)):
    conn = get_db_conn()
    # 手动添加只存基础的，不推荐
    cookies_json = json.dumps({"__Secure-1PSID": p1, "__Secure-1PSIDTS": p2})
    conn.execute("INSERT INTO accounts (name, cookie_1psid, cookie_1psidts, cookie_json) VALUES (?, ?, ?, ?)", 
                 (name, p1, p2, cookies_json))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/", status_code=303)

@app.get("/account/delete/{id}")
async def delete_account(id: int):
    conn = get_db_conn()
    conn.execute("DELETE FROM accounts WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/", status_code=303)

@app.get("/account/toggle/{id}")
async def toggle_account(id: int):
    conn = get_db_conn()
    conn.execute("UPDATE accounts SET is_active = NOT is_active WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/", status_code=303)

@app.get("/logs/clear")
async def clear_logs():
    conn = get_db_conn()
    conn.execute("DELETE FROM logs")
    conn.commit()
    conn.close()
    return RedirectResponse(url="/", status_code=303)

@app.post("/api/extract-cookies")
async def extract_cookies_from_har(request: Request):
    try:
        data = await request.json()
        raw_text = data.get("content", "")
        if not raw_text.strip():
            return JSONResponse(status_code=400, content={"success": False, "error": "内容为空"})
        
        success, session_data, logs = parse_and_validate(raw_text)
        
        if success and session_data:
            cookies = session_data.get("cookies", {})
            return JSONResponse({
                "success": True,
                "data": {"cookies": cookies},
                "logs": logs
            })
        else:
            return JSONResponse({"success": False, "error": "解析失败", "logs": logs})
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.post("/api/auto-add-account")
async def auto_add_account(request: Request):
    """自动添加从 HAR/请求中提取的账号（带自动验证功能）"""
    try:
        data = await request.json()
        name = data.get("name", "自动提取账号")
        cookies = data.get("cookies", {})
        
        # 1. 获取必要的 Cookie
        cookie_1psid = cookies.get("__Secure-1PSID", "")
        cookie_1psidts = cookies.get("__Secure-1PSIDTS", "")
        cookie_1psidcc = cookies.get("__Secure-1PSIDCC", "")
        
        if not cookie_1psid or not cookie_1psidts:
            return JSONResponse({
                "success": False,
                "error": "缺少必要的 Cookie",
                "message": "需要 __Secure-1PSID 和 __Secure-1PSIDTS"
            })

        # 2. 🔍【关键步骤】在存入数据库前，先进行连通性测试
        logger.info(f"🧪 正在验证账号 [{name}] 的有效性...")
        
        try:
            # 初始化临时客户端
            # 修改点：使用 proxy 字符串参数
            temp_client = GeminiClient(
                secure_1psid=cookie_1psid,
                secure_1psidts=cookie_1psidts,
                proxy=settings.PROXY_URL
            )
            
            # 手动注入 CC Cookie
            if cookie_1psidcc:
                temp_client.cookies["__Secure-1PSIDCC"] = cookie_1psidcc

            # ⚡ 尝试连接 Google (设置 30 秒超时)
            await temp_client.init(timeout=30, auto_close=True)
            logger.success(f"✅ 账号 [{name}] 验证通过！")
            
        except Exception as verify_err:
            error_str = str(verify_err)
            logger.error(f"❌ 账号验证失败: {error_str}")
            
            # 🔍 执行详细调试
            logger.info(f"🔍 开始对账号 [{name}] 进行详细诊断...")
            debug_cookies = {
                "__Secure-1PSID": cookie_1psid,
                "__Secure-1PSIDTS": cookie_1psidts
            }
            if cookie_1psidcc:
                debug_cookies["__Secure-1PSIDCC"] = cookie_1psidcc
                
            # 使用 asyncio.run 来运行异步调试函数
            try:
                debug_result = await debug_google_connection(name, debug_cookies, settings.PROXY_URL)
            except Exception as debug_err:
                logger.error(f"🔍 诊断过程出错: {debug_err}")
                debug_result = {"error": f"诊断失败: {debug_err}"}
            
            # 分析错误原因，返回给前端
            tips = "未知错误"
            if "Cookies invalid" in error_str:
                tips = "Cookie 无效。请确保复制了 StreamGenerate 请求，并且是在无痕模式下操作。"
            elif "Timeout" in error_str or "ConnectError" in error_str:
                tips = "连接 Google 超时。请检查代理是否稳定 (推荐美国节点)。"
            elif "location" in error_str.lower():
                tips = "IP 地区可能不支持。请更换美国/新加坡节点。"
            
            # 构建详细的错误信息
            detailed_msg = f"🚫 无法连接 Google：{tips}\n\n技术报错: {error_str}\n\n🔍 诊断信息:\n"
            
            if debug_result["ip_info"]:
                ip_info = debug_result["ip_info"]
                detailed_msg += f"• IP: {ip_info.get('query')} ({ip_info.get('countryCode', '未知')})\n"
            
            if debug_result["gemini_response"]:
                gemini_resp = debug_result["gemini_response"]
                detailed_msg += f"• Gemini 响应: {gemini_resp['status_code']}\n"
                if gemini_resp["status_code"] == 302:
                    detailed_msg += f"• 重定向到: {gemini_resp['headers'].get('Location', '未知')}\n"
                elif gemini_resp["status_code"] == 200 and not gemini_resp["has_snlM0e"]:
                    detailed_msg += "• 未找到 SNlM0e Token\n"
            
            if debug_result["error"]:
                detailed_msg += f"• 错误: {debug_result['error']}\n"

            return JSONResponse({
                "success": False, 
                "error": "验证失败", 
                "message": detailed_msg
            })

        # 3. 验证通过，存入数据库
        conn = get_db_conn()
        cursor = conn.cursor()
        
        # 检查是否已存在
        existing = cursor.execute(
            "SELECT id FROM accounts WHERE cookie_1psid = ?", 
            (cookie_1psid,)
        ).fetchone()
        
        if existing:
            conn.close()
            return JSONResponse({
                "success": False,
                "error": "账号已存在",
                "message": "该 Cookie 对应的账号已经添加过了"
            })
        
        # 插入新账号 (包含 1PSIDCC)
        cursor.execute(
            "INSERT INTO accounts (name, cookie_1psid, cookie_1psidts, cookie_1psidcc) VALUES (?, ?, ?, ?)",
            (name, cookie_1psid, cookie_1psidts, cookie_1psidcc)
        )
        conn.commit()
        conn.close()
        
        return JSONResponse({
            "success": True,
            "message": f"🎉 账号 '{name}' 验证通过并添加成功！",
            "account_id": cursor.lastrowid
        })
        
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.post("/v1/chat/completions")
async def chat_api(request: Request):
    data = await request.json()
    is_stream = data.get("stream", False)
    try:
        return await process_gemini_request(data, is_stream)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": {"message": str(e), "type": "internal_error"}})

@app.get("/v1/models")
async def models_api():
    current_time = int(time.time())
    return {
        "object": "list",
        "data": [{"id": "gemini-2.5-pro", "created": current_time, "object": "model", "owned_by": "google"}]
    }

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=settings.PORT, reload=False)