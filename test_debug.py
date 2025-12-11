import asyncio
import sys
sys.path.append('.')

from main import debug_google_connection

async def test_debug():
    # 测试用的 Cookie（请替换为实际的）
    test_cookies = {
        "__Secure-1PSID": "test_value",
        "__Secure-1PSIDTS": "test_value",
    }
    
    proxy = "http://127.0.0.1:7890"
    
    print("开始测试调试功能...")
    result = await debug_google_connection("测试账号", test_cookies, proxy)
    
    print("\n=== 诊断结果 ===")
    print(f"IP 信息: {result.get('ip_info')}")
    print(f"Gemini 响应: {result.get('gemini_response')}")
    print(f"错误: {result.get('error')}")

if __name__ == "__main__":
    asyncio.run(test_debug())