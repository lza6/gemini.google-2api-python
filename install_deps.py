import subprocess
import sys

def install(package):
    """安装 Python 包"""
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])

def main():
    print("正在安装必要的依赖...")
    
    packages = [
        "fastapi",
        "uvicorn[standard]",
        "pydantic-settings",
        "python-dotenv",
        "httpx",
        "loguru",
        "jinja2",
        "requests",
        "gemini_webapi>=1.17.3"
    ]
    
    for package in packages:
        try:
            print(f"安装 {package}...")
            install(package)
            print(f"✅ {package} 安装成功")
        except Exception as e:
            print(f"❌ {package} 安装失败: {e}")
    
    print("\n所有依赖安装完成！现在可以运行 start.bat 启动服务器了。")

if __name__ == "__main__":
    main()