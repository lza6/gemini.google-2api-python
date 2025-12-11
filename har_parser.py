import json
import re
from urllib.parse import urlparse, parse_qs, unquote
from typing import Dict, Any, Optional, Tuple, List

def extract_best_json(text: str) -> Optional[Dict]:
    """
    从混乱的文本中提取最大/最可能的有效 JSON 对象。
    解决了直接正则匹配在包含多个花括号或日志头时失败的问题。
    """
    text = text.strip().replace('\ufeff', '')
    
    # 1. 尝试直接解析
    try:
        return json.loads(text)
    except:
        pass

    # 2. 尝试寻找最外层的 {}
    starts = [m.start() for m in re.finditer(r'\{', text)]
    
    if not starts:
        return None

    # 从最早的起始点开始，尝试寻找能解析的 JSON
    for start in starts:
        # 尝试匹配到字符串末尾的最后一个 }
        end_search = text.rfind('}')
        if end_search == -1 or end_search < start:
            continue
            
        candidate_str = text[start : end_search + 1]
        
        # 优化：尝试去除 JSON 之前的 BOM 或其他非 JSON 字符
        if candidate_str.startswith(')]}\''):
            candidate_str = candidate_str[4:]
        
        try:
            data = json.loads(candidate_str)
            # 确保是字典类型
            if isinstance(data, dict):
                return data 
        except:
            continue
            
    return None

def parse_cookies_from_header_list(headers: List[Dict]) -> Dict[str, str]:
    """从 HAR 格式的 headers 列表中提取 Cookie"""
    cookie_str = ""
    for header in headers:
        # 忽略大小写查找 'Cookie' 头
        if header.get('name', '').lower() == 'cookie':
            cookie_str = header.get('value', '')
            break
    return parse_cookies_from_string(cookie_str)

def parse_cookies_from_string(cookie_string: str) -> Dict[str, str]:
    """从 Cookie 字符串中提取关键 Cookie。"""
    if not cookie_string:
        return {}
        
    # 增加更多相关的 Cookie 名称以提高成功率
    target_keywords = ['PSID', 'PSIDTS', 'SID', 'APISID', 'HSID', 'SSID', 'ENID', 'AEC', 'NID', 'SIDCC']
    
    cookies = {}
    # 先清理字符串，移除可能的 JSON 内容
    cookie_string = cookie_string.split('"}')[0].split('"]')[0]
    
    # 处理可能的分隔符：分号后可能跟空格，也可能没有
    parts = cookie_string.split(';')
    for pair in parts:
        if '=' in pair:
            name, value = pair.split('=', 1)
            name = name.strip()
            value = value.strip()
            
            # 额外清理：移除 value 中的引号和多余字符
            value = value.strip('"\'')  # 移除首尾的引号
            # 移除可能的 JSON 结束符
            for sep in ['"}', '"]', '}','\n', '\r', '\t']:
                if sep in value:
                    value = value.split(sep)[0]
            
            # 检查是否包含目标关键词
            if any(keyword in name for keyword in target_keywords):
                cookies[name] = value
                 
    return cookies

def parse_har_content(har_content: str) -> Tuple[bool, Optional[Dict], str]:
    """解析 HAR 文件内容，并返回日志。"""
    log_messages = ["-> 尝试使用标准 HAR/JSON 解析..."]
    
    data = extract_best_json(har_content)
    if not data:
        log_messages.append("    [失败] 未找到有效的 JSON 结构。")
        return (False, None, "\n".join(log_messages))
        
    target_entry = None
    
    # 递归查找包含特定 URL 的 request 对象
    def find_entry(obj):
        if isinstance(obj, dict):
            if 'url' in obj and ('/StreamGenerate' in obj['url'] or 'f.sid' in obj['url']):
                return obj
            if 'request' in obj:
                res = find_entry(obj['request'])
                if res: return res
            
            for key, value in obj.items():
                if isinstance(value, (dict, list)):
                    res = find_entry(value)
                    if res: return res
        elif isinstance(obj, list):
            for item in obj:
                res = find_entry(item)
                if res: return res
        return None

    # 优先检查标准的 log -> entries 结构
    if isinstance(data, dict) and 'log' in data and 'entries' in data['log']:
        for entry in reversed(data['log']['entries']):
            if 'request' in entry and 'url' in entry['request']:
                if '/StreamGenerate' in entry['request']['url'] and entry['request'].get('method') == 'POST':
                    target_entry = entry['request']
                    break
    
    if not target_entry:
        target_entry = find_entry(data)

    if not target_entry:
        log_messages.append("    [失败] 未找到 StreamGenerate API 请求记录。")
        return (False, None, "\n".join(log_messages)) 
    
    log_messages.append("    [成功] 找到目标 API 请求记录。")

    # 1. 提取 f.sid
    url_parsed = urlparse(target_entry.get('url', ''))
    query_params = parse_qs(url_parsed.query)
    f_sid = query_params.get('f.sid', [None])[0]
    
    # 2. 提取 at
    at_param = None
    post_data = target_entry.get('postData', {})
    if post_data.get('text'):
        text_data = post_data.get('text', '')
        if 'application/x-www-form-urlencoded' in post_data.get('mimeType', ''):
             params = parse_qs(text_data)
             at_param_encoded = params.get('at', [None])[0]
             at_param = unquote(at_param_encoded) if at_param_encoded else None
        
        if not at_param:
            at_match = re.search(r'at=([^&]+)', text_data)
            if at_match:
                 at_param = unquote(at_match.group(1))

    # 3. 提取 Cookies - 增强版本
    extracted_cookies = {}
    
    # 方法1: 从 headers 中查找 Cookie
    if 'headers' in target_entry:
        extracted_cookies = parse_cookies_from_header_list(target_entry['headers'])
        log_messages.append(f"    [调试] 从 headers 提取到 {len(extracted_cookies)} 个 Cookie")
    
    # 方法2: 如果 headers 没找到，尝试从 cookies 字段获取
    if not extracted_cookies and 'cookies' in target_entry and isinstance(target_entry['cookies'], list):
        temp_cookie_str = ""
        for c in target_entry['cookies']:
             temp_cookie_str += f"{c['name']}={c['value']}; "
        extracted_cookies = parse_cookies_from_string(temp_cookie_str)
        log_messages.append(f"    [调试] 从 cookies 字段提取到 {len(extracted_cookies)} 个 Cookie")
    
    # 方法3: 如果还是没有，在整个 HAR 文件中搜索 Cookie
    if not extracted_cookies:
        # 递归搜索整个 HAR 文件中的所有 Cookie
        def find_all_cookies(obj):
            cookies = {}
            if isinstance(obj, dict):
                # 检查是否有 cookie 相关字段
                for key in ['cookie', 'Cookie', 'cookies']:
                    if key in obj:
                        if isinstance(obj[key], str):
                            cookies.update(parse_cookies_from_string(obj[key]))
                        elif isinstance(obj[key], list):
                            for item in obj[key]:
                                if isinstance(item, dict) and 'name' in item and 'value' in item:
                                    cookies[item['name']] = item['value']
                
                # 递归搜索
                for value in obj.values():
                    if isinstance(value, (dict, list)):
                        cookies.update(find_all_cookies(value))
            elif isinstance(obj, list):
                for item in obj:
                    if isinstance(item, (dict, list)):
                        cookies.update(find_all_cookies(item))
            return cookies
        
        all_cookies = find_all_cookies(data)
        # 只保留我们需要的 Cookie
        for name in all_cookies:
            if any(keyword in name for keyword in ['PSID', 'PSIDTS', 'SID', 'APISID']):
                extracted_cookies[name] = all_cookies[name]
        
        log_messages.append(f"    [调试] 从整个 HAR 文件搜索到 {len(extracted_cookies)} 个关键 Cookie")

    if not f_sid or not at_param:
        log_messages.append(f"    [失败] 动态参数提取不完整 (fSid: {f_sid}, at: {at_param})。")
        return (False, None, "\n".join(log_messages)) 
    log_messages.append(f"    [成功] 提取到 f.sid 和 at 动态参数。")
    log_messages.append(f"    [状态] 提取到 {len(extracted_cookies)} 个关键 Cookie。")

    if len(extracted_cookies) == 0:
        log_messages.append("    [⚠️ 警告] 请求头中未发现关键 Cookie！")
        
    return (True, {
        "cookies": extracted_cookies,
        "dynamicParams": {
            "fSid": f_sid,
            "at": at_param
        }
    }, "\n".join(log_messages))

def parse_text_segments(text_content: str) -> Tuple[bool, Optional[Dict], str]:
    """解析非标准分段文本，并返回日志。"""
    log_messages = ["-> 尝试使用非标准分段文本/正则解析..."]
    
    # 1. 提取 URL (f.sid)
    url_match = re.search(r'(https?://[^\s]*(?:StreamGenerate|StreamGenerate\?)[^\s]*)', text_content)
    f_sid = None
    
    if url_match:
        full_url = url_match.group(1)
        log_messages.append(f"    [成功] 提取到 URL: {full_url[:60]}...")
        url_parsed = urlparse(full_url)
        query_params = parse_qs(url_parsed.query)
        f_sid = query_params.get('f.sid', [None])[0]
    else:
        # 备用：直接在文本中搜索 f.sid
        sid_match = re.search(r'f\.sid\s*[:=]\s*([-0-9]+)', text_content)
        if sid_match:
             f_sid = sid_match.group(1)
             log_messages.append(f"    [成功] 直接正则提取到 f.sid: {f_sid}")

    # 2. 提取 at 参数
    at_param = None
    at_match = re.search(r'at=([^&\s]+)', text_content)
    if not at_match:
        at_match = re.search(r'at\s*[:=]\s*([^\s"]+)', text_content)
    
    if at_match:
        raw_at = at_match.group(1).strip()
        if '%' in raw_at and raw_at.startswith('A'):
            at_param = unquote(raw_at)
        else:
            at_param = raw_at
    
    # 3. 提取 Cookie
    cookie_header_value = ""
    cookie_match = re.search(r'(?:Cookie|cookie):\s*([^\r\n]+)', text_content, re.IGNORECASE)
    if cookie_match:
        cookie_header_value = cookie_match.group(1).strip()
    elif 'SID=' in text_content and '__Secure-1PSID=' in text_content:
        # 如果用户只粘贴了 Cookie 字符串
         cookie_header_value = text_content 

    extracted_cookies = parse_cookies_from_string(cookie_header_value)

    if not f_sid or not at_param:
        log_messages.append(f"    [失败] 动态参数提取不完整 (fSid found: {bool(f_sid)}, at found: {bool(at_param)})。")
        return (False, None, "\n".join(log_messages))
    
    log_messages.append(f"    [成功] 提取到 f.sid 和 at 动态参数。")
    log_messages.append(f"    [状态] 提取到 {len(extracted_cookies)} 个关键 Cookie。")
    
    if len(extracted_cookies) == 0:
        log_messages.append("    [⚠️ 警告] 未能提取到关键 Cookie。")
    
    return (True, {
        "cookies": extracted_cookies,
        "dynamicParams": {
            "fSid": f_sid,
            "at": at_param
        }
    }, "\n".join(log_messages))

def parse_and_validate(raw_text: str) -> Tuple[bool, Optional[Dict], str]:
    """
    尝试所有解析方法，返回结果和详细日志。
    """
    
    # 1. 尝试 HAR 文件/JSON 请求解析 (最优先)
    parsed_from_har = parse_har_content(raw_text)
    if parsed_from_har[0]:
        return (True, parsed_from_har[1], parsed_from_har[2] + "\n✅ 提取成功! (格式: HAR/JSON)")

    # 2. 尝试手动粘贴的分段文本解析 (正则兜底，兼容 cURL/Request Headers 格式)
    parsed_from_segments = parse_text_segments(raw_text)
    if parsed_from_segments[0]:
        return (True, parsed_from_segments[1], parsed_from_segments[2] + "\n✅ 提取成功! (格式: 正则文本)")
    
    # 全部失败，组合详细日志
    final_log = "\n--- ❌ 提取失败：详细解析日志 ---\n" + \
                "--- 1. HAR/JSON 解析尝试 --- \n" + parsed_from_har[2] + "\n" + \
                "--- 2. 分段文本解析尝试 --- \n" + parsed_from_segments[2] + "\n"
    
    return (False, None, final_log + "\n❌ 粘贴的内容解析失败。请确保您粘贴了包含 StreamGenerate 请求的完整内容。")