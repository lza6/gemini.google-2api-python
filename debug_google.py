import requests
import json

# ================= 配置区 =================
# 1. 填入你的代理
PROXIES = {
    "http": "http://127.0.0.1:7890",
    "https": "http://127.0.0.1:7890"
}

# 2. 填入你刚才提取的 Cookie (只需要这两个核心的测试)
COOKIES = {
    "__Secure-1PSID": "你的值",
    "__Secure-1PSIDTS": "你的值",
    # 如果有 CC 也填上
    "__Secure-1PSIDCC": "你的值" 
}

# 3. 伪装头 (和 main.py 保持一致)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
}
# =========================================

def diagnose():
    print("1. 正在检查代理 IP 归属...")
    try:
        ip_info = requests.get("http://ip-api.com/json", proxies=PROXIES, timeout=10).json()
        print(f"   当前 IP: {ip_info.get('query')} | 地区: {ip_info.get('countryCode')} ({ip_info.get('country')})")
        if ip_info.get('countryCode') in ['CN', 'HK']:
            print("   ⚠️ 警告: 香港/中国节点极易导致 Google 验证失败！请切换至 US/SG。")
    except Exception as e:
        print(f"   ⚠️ 代理连接失败: {e}")
        return

    print("\n2. 正在尝试直连 Gemini 首页 (验证 Cookie)...")
    try:
        # 访问 Gemini App 首页，库的 init() 就是在做这个
        resp = requests.get(
            "https://gemini.google.com/app", 
            cookies=COOKIES, 
            headers=HEADERS, 
            proxies=PROXIES,
            timeout=20,
            allow_redirects=False # 禁止自动跳转，我们要看是不是被重定向了
        )
        
        print(f"   HTTP 状态码: {resp.status_code}")
        
        if resp.status_code == 302:
            print("   ❌ 失败: 被重定向了 (通常是跳回登录页)。")
            print(f"   跳转目标: {resp.headers.get('Location')}")
            print("   👉 结论: Cookie 失效。请尝试【停止加载大法】重新提取。")
            
        elif resp.status_code == 200:
            # 检查关键 Token 是否存在
            if "SNlM0e" in resp.text:
                print("   ✅ 成功: 找到了 'SNlM0e' Token！环境配置没问题。")
                print("   👉 建议: 既然脚本能通，请确保 data.db 已删除，并重启主程序再试。")
            else:
                print("   ❓ 存疑: 状态码 200 但没找到 Token。可能是 Google 返回了验证码页面。")
                print("   页面标题: ", resp.text.split('<title>')[1].split('</title>')[0] if '<title>' in resp.text else '无标题')
                
        else:
            print(f"   ❌ 失败: 异常状态码。")
    
    except Exception as e:
        print(f"   ❌ 请求发生错误: {e}")

if __name__ == "__main__":
    diagnose()