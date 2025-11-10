import re
import time
from sign_py import getSign
import requests
import hashlib
import os
import json
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
try:
    import readline
except ImportError:
    readline = None
from fnmatch import fnmatch

# ================================= 新版：路径式操作 =================================
class Pan123:
    def __init__(
        self,
        readfile=True,
        user_name="",
        pass_word="",
        authorization="",
        input_pwd=True,
    ):
        self.recycle_list = None
        self.list = []  # 当前目录列表
        self.dir_cache = {}  # file_id -> list[children]
        self.path_cache = {}  # 绝对路径 -> fileInfo
        self.current_dir_id = 0
        self.path_stack = ["/"]  # 显示路径
        self.last_api_error_code = 0  # 最近一次目录请求错误码

        self.header_only_usage = {
            "user-agent": "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/"
            "537.36 (KHTML, like Gecko) Chrome/109.0.0.0 "
            "Safari/537.36 Edg/109.0.1474.0",
            "app-version": "2",
            "platform": "web",
        }
        self.header_logined = {
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
            "App-Version": "3",
            "Authorization": "", # 初始化时为空
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "LoginUuid": "z-uk_yT8HwR4raGX1gqGk",
            "Pragma": "no-cache",
            "Referer": "https://www.123pan.com/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/"
            "537.36 (KHTML, like Gecko) Chrome/119.0.0.0 "
            "Safari/537.36 Edg/119.0.0.0",
            "platform": "web",
            "sec-ch-ua": "^\\^Microsoft",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "^\\^Windows^^",
        }

        if readfile:
            self.read_ini(user_name, pass_word, input_pwd, authorization)
        else:
            if user_name == "" or pass_word == "":
                print("读取已禁用，用户名或密码为空")
                if input_pwd:
                    user_name = input("请输入用户名:")
                    pass_word = input("请输入密码:")
                else:
                    raise Exception("用户名或密码为空：读取禁用时，userName和passWord不能为空")
            self.user_name = user_name
            self.password = pass_word
            self.authorization = authorization
            self.header_logined["Authorization"] = self.authorization

        # 初始化目录与自动登录
        init_code = self.get_dir(self.current_dir_id)
        if init_code != 0 or not self.list:
            print("初始化列表失败或为空，尝试登录...")
            if self.login() == 0:
                self.get_dir(self.current_dir_id)

        self.download_dir = os.path.abspath('.')
        self.max_workers = 3
        self.lock_print = threading.Lock()
        self.base_commands = [
            'ls','cd','pwd','download','link','upload','share','delete','mkdir',
            'reload','log','help','exit','setdir','re'
        ]
        if readline:
            self._setup_completion()

    # ------------------ API 基础 ------------------
    def login(self):
        data = {"remember": True, "passport": self.user_name, "password": self.password}
        sign = getSign("/b/api/user/sign_in")
        login_res = requests.post(
            "https://www.123pan.com/b/api/user/sign_in",
            headers=self.header_only_usage,
            data=data,
            params={sign[0]: sign[1]}, timeout=10
        )
        res_sign = login_res.json()
        res_code_login = res_sign["code"]
        if res_code_login != 200:
            print("登录失败:", res_sign.get("message"))
            return res_code_login
        token = res_sign["data"]["token"]
        self.authorization = "Bearer " + token
        self.header_logined["Authorization"] = self.authorization
        self.save_file()
        return 0

    def save_file(self):
        with open("123pan.txt", "w", encoding="utf_8") as f:
            f.write(json.dumps({
                "userName": self.user_name,
                "passWord": self.password,
                "authorization": self.authorization,
            }))
        print("账号已保存")

    def api_list(self, parent_id: int):
        if parent_id in self.dir_cache:
            return self.dir_cache[parent_id]
        page = 1
        items = []
        l_now = 0
        total = -1
        while l_now < total or total == -1:
            sign = getSign("/b/api/file/list/new")
            params = {
                sign[0]: sign[1],
                "driveId": 0,
                "limit": 100,
                "next": 0,
                "orderBy": "file_id",
                "orderDirection": "desc",
                "parentFileId": str(parent_id),
                "trashed": False,
                "SearchData": "",
                "Page": str(page),
                "OnlyLookAbnormalFile": 0,
            }
            resp = requests.get("https://www.123pan.com/b/api/file/list/new", headers=self.header_logined, params=params, timeout=10)
            data = resp.json()
            code = data.get("code", -1)
            self.last_api_error_code = code
            if code != 0:
                # 401 等错误直接中断
                break
            page_list = data["data"]["InfoList"]
            items += page_list
            total = data["data"]["Total"]
            l_now += len(page_list)
            page += 1
        self.dir_cache[parent_id] = items
        return items

    def get_dir(self, parent_id: int):
        items = self.api_list(parent_id)
        if self.last_api_error_code != 0:
            # token 失效或未登录，尝试自动登录后重试一次
            if self.last_api_error_code in (401, 402, 403, 10002):
                print("检测到未登录或 token 失效，自动登录中…")
                if self.login() == 0:
                    # 清除旧缓存重试
                    self.dir_cache.pop(parent_id, None)
                    items = self.api_list(parent_id)
            else:
                print("获取目录失败 code=", self.last_api_error_code)
        self.list = items
        return self.last_api_error_code

    # ------------------ 路径解析 ------------------
    def normalize(self, p: str):
        if not p:
            return '/'
        if not p.startswith('/'):
            # 相对路径
            base = '/' if len(self.path_stack)==1 else '/'.join(self.path_stack).rstrip('/')
            if base == '/':
                p = '/' + p
            else:
                p = base + '/' + p
        # 处理 .. 和 .
        parts = []
        for seg in p.split('/'):
            if seg in ('', '.'):
                continue
            if seg == '..':
                if parts:
                    parts.pop()
                continue
            parts.append(seg)
        return '/' + '/'.join(parts)

    def resolve(self, path: str):
        path = self.normalize(path)
        if path == '/':
            return {'FileId': 0, 'FileName': '/', 'Type': 1}
        # 分段逐层查询
        segments = path.strip('/').split('/')
        current_id = 0
        node = None
        built = []
        for seg in segments:
            children = self.api_list(current_id)
            found = None
            for ch in children:
                if ch['FileName'] == seg:
                    found = ch
                    break
            if not found:
                return None
            node = found
            current_id = found['FileId']
            built.append(seg)
        # 缓存
        self.path_cache[path] = node
        return node

    def re(self):
        """刷新当前目录缓存"""
        self.dir_cache.pop(self.current_dir_id, None)
        self.path_cache.clear() # 路径缓存也应清理
        print("当前目录缓存已刷新")
        self.get_dir(self.current_dir_id)
        self.ls()

    # ------------------ 显示与导航 ------------------
    def ls(self, path: str = ''):
        target_path = path if path else self.pwd()
        node = self.resolve(target_path)
        if node is None or node['Type'] != 1:
            print('路径不存在或不是目录')
            return
        file_id = node['FileId']
        items = self.api_list(file_id)
        if not items and self.last_api_error_code != 0:
             print(f"获取目录失败: code={self.last_api_error_code}")
             return
        print(f"\n目录: {self.normalize(target_path)}  共 {len(items)} 项")
        for ch in items:
            size = ch['Size']
            if ch['Type'] == 1:
                mark = 'd'
                size_str = '-'  # 目录无大小
                color = '\033[35m'
            else:
                mark = 'f'
                if size > 1048576:
                    size_str = f"{round(size/1048576,2)}M"
                else:
                    size_str = f"{round(size/1024,2)}K"
                color = '\033[33m'
            print(f"{color}{mark}  {ch['FileName']:<40} {size_str:>8}\033[0m")
        print("")

    def cd(self, path: str):
        node = self.resolve(path)
        if not node:
            print('路径不存在')
            return
        if node['Type'] != 1:
            print('不是目录')
            return
        self.current_dir_id = node['FileId']
        norm = self.normalize(path)
        self.path_stack = ['/' ] + norm.strip('/').split('/') if norm != '/' else ['/']
        self.get_dir(self.current_dir_id)
        print('进入', norm)

    def pwd(self):
        if len(self.path_stack) == 1:
            return '/'
        return '/' + '/'.join(self.path_stack[1:])

    # ------------------ 下载 / 链接 ------------------
    def link_path(self, path: str, showlink=True):
        node = self.resolve(path)
        if not node:
            print('文件不存在')
            return None
        if node['Type'] == 1:
            down_request_url = "https://www.123pan.com/a/api/file/batch_download_info"
            down_request_data = {"fileIdList": [{"fileId": int(node["FileId"])}]}
            sign_key = "/a/api/file/download_info"  # 兼容接口
        else:
            down_request_url = "https://www.123pan.com/a/api/file/download_info"
            down_request_data = {
                "driveId": 0,
                "etag": node["Etag"],
                "fileId": node["FileId"],
                "s3keyFlag": node["S3KeyFlag"],
                "type": node["Type"],
                "fileName": node["FileName"],
                "size": node["Size"],
            }
            sign_key = "/a/api/file/download_info"
        sign = getSign(sign_key)
        link_res = requests.post(
            down_request_url,
            headers=self.header_logined,
            params={sign[0]: sign[1]},
            data=down_request_data,
            timeout=10
        )
        res_code_download = link_res.json().get("code", -1)
        if res_code_download != 0:
            print("获取下载信息失败:", link_res.text)
            return None
        download_link_base64 = link_res.json()["data"]["DownloadUrl"]
        base64_url = re.findall("params=(.*)&", download_link_base64)[0]
        down_load_url = base64.b64decode(base64_url).decode("utf-8")
        redirect_json = requests.get(down_load_url, timeout=10).json()
        redirect_url = redirect_json["data"]["redirect_url"]
        if showlink:
            print(redirect_url)
        return redirect_url

    def download_path(self, path: str, local_dir: str = None):
        node = self.resolve(path)
        if not node:
            print('文件不存在')
            return
        if node['Type'] == 1:
            print('暂不支持目录直接下载(需打包逻辑)')
            return
        url = self.link_path(path, showlink=False)
        if not url:
            return
        file_name = node['FileName']
        
        # 使用指定的 local_dir 或默认的 download_dir
        download_to_dir = local_dir if local_dir is not None else self.download_dir
        if not os.path.isdir(download_to_dir):
            try:
                os.makedirs(download_to_dir)
                print(f"创建本地目录: {download_to_dir}")
            except OSError as e:
                print(f"创建本地目录失败: {e}")
                return

        target_path = os.path.join(download_to_dir, file_name)
        if os.path.exists(target_path):
            print('文件已存在，跳过:', target_path)
            return
        try:
            down = requests.get(url, stream=True, timeout=10)
        except Exception as e:
            print('下载失败:', e)
            return
        size = int(down.headers.get('Content-Length', 0))
        if size > 1048576:
            size_str = f"{round(size/1048576,2)}M"
        else:
            size_str = f"{round(max(size,1)/1024,2)}K"
        print(f"开始下载: {file_name} 大小 {size_str}")
        data_count = 0
        t0 = time.time()
        t_prev = t0
        dc_prev = 0
        with open(target_path, 'wb') as f:
            for chunk in down.iter_content(64*1024):
                if not chunk:
                    break
                f.write(chunk)
                data_count += len(chunk)
                if time.time() - t_prev >= 1:
                    pass_data = data_count - dc_prev
                    dc_prev = data_count
                    speed = pass_data / (time.time() - t_prev + 1e-6)
                    t_prev = time.time()
                    speed_m = speed/1048576
                    speed_str = f"{speed_m:.2f}M/s" if speed_m > 1 else f"{speed_m*1024:.2f}K/s"
                    percent = data_count/size*100 if size else 0
                    bar_len = 30
                    done = int(percent/100*bar_len)
                    bar = '█'*done + ' '*(bar_len-done)
                    print(f"\r[{bar}] {percent:5.1f}% {speed_str} ", end='')
        print("\n完成", file_name)

    def download_many_paths(self, paths: list, local_dir: str = None):
        files = []
        for p in paths:
            node = self.resolve(p)
            if node and node['Type']==0:
                files.append(p)
            else:
                print('跳过(不存在或非文件):', p)
        if not files:
            print('没有有效文件')
            return
        print('批量下载文件数:', len(files))
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = [pool.submit(self.download_path, fp, local_dir) for fp in files]
            for _ in as_completed(futures):
                pass
        print('批量下载完成')

    # ------------------ 目录收集（支持目录递归下载） ------------------
    def collect_files(self, path: str):
        """将路径扩展为文件列表; 若是文件直接返回列表; 若是目录递归收集内部所有文件"""
        node = self.resolve(path)
        if not node:
            print('路径不存在:', path)
            return []
        norm = self.normalize(path)
        if node['Type'] == 0:
            return [norm]
        # 目录递归
        return self._collect_dir(node['FileId'], norm)

    def _collect_dir(self, dir_id: int, base_path: str):
        files = []
        for ch in self.api_list(dir_id):
            child_path = base_path.rstrip('/') + '/' + ch['FileName']
            if ch['Type'] == 0:
                files.append(child_path)
            else:
                files.extend(self._collect_dir(ch['FileId'], child_path))
        return files

    # ------------------ 其它操作 ------------------
    def delete_path(self, path: str):
        node = self.resolve(path)
        if not node:
            print('路径不存在')
            return
        data_delete = {
            "driveId": 0,
            "fileTrashInfoList": node,
            "operation": True,
        }
        resp = requests.post("https://www.123pan.com/a/api/file/trash", data=json.dumps(data_delete), headers=self.header_logined, timeout=10)
        js = resp.json()
        print(js.get('message'))
        # 刷新当前目录缓存
        self.dir_cache.pop(self.current_dir_id, None)
        self.get_dir(self.current_dir_id)

    def mkdir(self, dirname: str):
        if not dirname:
            print('目录名为空')
            return
        # 支持路径形式 a/b/c
        norm = self.normalize(dirname)
        parent_path = '/' + '/'.join(norm.strip('/').split('/')[:-1]) if '/' in norm.strip('/') else '/'
        dir_name = norm.strip('/').split('/')[-1]
        parent_node = self.resolve(parent_path)
        if not parent_node or parent_node['Type']!=1:
            print('父目录不存在')
            return
        # 检测是否存在
        for ch in self.api_list(parent_node['FileId']):
            if ch['FileName'] == dir_name and ch['Type']==1:
                print('目录已存在')
                return
        data_mk = {
            "driveId": 0,
            "etag": "",
            "fileName": dir_name,
            "parentFileId": parent_node['FileId'],
            "size": 0,
            "type": 1,
            "duplicate": 1,
            "NotReuse": True,
            "event": "newCreateFolder",
            "operateType": 1,
        }
        sign = getSign("/a/api/file/upload_request")
        res_mk = requests.post(
            "https://www.123pan.com/a/api/file/upload_request",
            headers=self.header_logined,
            data=json.dumps(data_mk),
            params={sign[0]: sign[1]},
            timeout=10
        )
        try:
            js = res_mk.json()
        except:
            print('创建失败:', res_mk.text)
            return
        if js.get('code')==0:
            print('创建成功')
            self.dir_cache.pop(parent_node['FileId'], None)
        else:
            print('创建失败:', js)

    def read_ini(self, user_name, pass_word, input_pwd, authorization=""):
        try:
            with open("123pan.txt", "r", encoding="utf-8") as f:
                txt = json.loads(f.read())
            self.user_name = txt["userName"]
            self.password = txt["passWord"]
            self.authorization = txt["authorization"]
            if self.authorization:
                self.header_logined["Authorization"] = self.authorization
            else:
                print("配置中无有效 token，准备登录…")
                self.login()
        except Exception:
            print('配置读取失败')
            if (not user_name or not pass_word) and input_pwd:
                self.user_name = input('userName:')
                self.password = input('passWord:')
                self.authorization = ''
                self.login()
            else:
                self.user_name = user_name
                self.password = pass_word
                self.authorization = authorization
                if not self.authorization:
                    self.login()

    # ------------------ 补全 ------------------
    def _setup_completion(self):
        def completer(text, state):
            line = readline.get_line_buffer()
            parts = line.split()
            stripped = line.strip()
            # 基础命令补全（首参数）
            if ' ' not in stripped and not line.endswith(' '):
                cands = [c for c in self.base_commands if c.startswith(text)]
                try: return cands[state]
                except IndexError: return None
            if not parts:
                return None
            cmd = parts[0]
            # -------- download 命令逻辑 --------
            if cmd == 'download':
                arg_text = line[len('download'):].lstrip()
                tokens = arg_text.split()
                # 当前光标所在 token
                current_token = '' if line.endswith(' ') else tokens[-1]
                # 判定是否进入本地路径补全：至少已有一个远程参数(可解析或含通配符)，且当前 token 不可解析为远程并且本地存在(或以 ~ 开头)
                has_remote = any(('*' in t or '?' in t or self.resolve(t) is not None) for t in tokens[:-1]) if len(tokens) > 1 else False
                is_local_candidate = (
                    has_remote and current_token and '*' not in current_token and '?' not in current_token and
                    self.resolve(current_token) is None and (
                        current_token.startswith('~') or os.path.exists(os.path.expanduser(current_token.rstrip('/')))
                    )
                )
                if is_local_candidate:
                    return self._local_path_completer(current_token, state)
                # 远程路径补全
                return self._remote_path_completer(current_token, state)
            # -------- 其它远程路径命令 --------
            if cmd in ('cd','ls','delete','link','mkdir'):
                arg_part = line[len(cmd):].lstrip()
                current_token = '' if line.endswith(' ') else arg_part.split()[-1]
                return self._remote_path_completer(current_token, state)
            # -------- setdir 本地路径补全 --------
            if cmd == 'setdir':
                arg_part = line[len(cmd):].lstrip()
                current_token = '' if line.endswith(' ') else arg_part.split()[-1]
                return self._local_path_completer(current_token, state)
            return None
        readline.set_completer(completer)
        readline.parse_and_bind('tab: complete')

    def _remote_path_completer(self, text, state):
        """远程路径补全（修正：目录后加 / 仅列该目录子项，前缀匹配不再回退根）"""
        # 空输入或仅 '/' -> 根目录子项
        if text in (None, '', '/'):  
            root_children = self.api_list(0)
            cands = []
            for ch in root_children:
                suff = '/' if ch['Type']==1 else ''
                cands.append(ch['FileName'] + suff)
            try: return cands[state]
            except IndexError: return None
        is_abs = text.startswith('/')
        norm = self.normalize(text)
        # 目录完整（以 / 结尾）：列出该目录子项
        if text.endswith('/'):
            base_dir = self.normalize(text.rstrip('/'))
            node = self.resolve(base_dir)
            if not node or node.get('Type')!=1:
                return None
            children = self.api_list(node['FileId'])
            cands = []
            for ch in children:
                suff = '/' if ch['Type']==1 else ''
                # 保持输入风格：相对输入返回相对路径，绝对输入返回绝对路径
                if is_abs:
                    show = base_dir.rstrip('/') + '/' + ch['FileName'] + suff if base_dir != '/' else '/' + ch['FileName'] + suff
                else:
                    # 去掉当前工作路径前缀
                    if base_dir == '/':
                        show = ch['FileName'] + suff
                    else:
                        rel_base = base_dir.lstrip('/')
                        show = rel_base + '/' + ch['FileName'] + suff
                cands.append(show)
            try: return cands[state]
            except IndexError: return None
        # 非结尾：做前缀匹配
        # 拆分父目录与前缀
        # 使用原始输入判断相对/绝对
        if '/' in text.rstrip('/'):
            parent_part = text.rstrip('/').rsplit('/',1)[0]
            prefix = text.rstrip('/').rsplit('/',1)[1]
        else:
            parent_part = ''
            prefix = text.rstrip('/')
        parent_path = self.normalize(parent_part) if parent_part else self.pwd()
        parent_node = self.resolve(parent_path)
        if not parent_node or parent_node.get('Type')!=1:
            return None
        children = self.api_list(parent_node['FileId'])
        cands = []
        for ch in children:
            if not prefix or ch['FileName'].startswith(prefix):
                suff = '/' if ch['Type']==1 else ''
                if is_abs or parent_part.startswith('/'):
                    # 绝对输入
                    if parent_path=='/':
                        show = '/' + ch['FileName'] + suff
                    else:
                        show = parent_path.rstrip('/') + '/' + ch['FileName'] + suff
                else:
                    # 相对输入
                    if parent_path=='/':
                        show = ch['FileName'] + suff
                    else:
                        show = parent_path.lstrip('/') + '/' + ch['FileName'] + suff
                cands.append(show)
        try: return cands[state]
        except IndexError: return None

    def _local_path_completer(self, text, state):
        """本地路径补全逻辑(改进: 合并重复斜杠, 支持 ~ 展开)"""
        import re as _re
        if text.startswith('~'):
            base = os.path.expanduser(text)
        else:
            base = text
        # 合并多余斜杠
        base = _re.sub(r'/+', '/', base)
        # 若是目录前缀(以 / 结尾)直接列其内容
        if os.path.isdir(base.rstrip('/')):
            dirname = base.rstrip('/')
        else:
            dirname = os.path.dirname(base) if os.path.dirname(base) else '.'
        prefix = os.path.basename(base) if not base.endswith('/') else ''
        try:
            entries = os.listdir(dirname or '.')
        except Exception:
            return None
        cands = []
        for e in entries:
            if not prefix or e.startswith(prefix):
                full = os.path.join(dirname, e)
                show = full
                if full.startswith('./'):
                    show = full[2:]
                if os.path.isdir(full):
                    show += '/'
                cands.append(show)
        try:
            return cands[state]
        except IndexError:
            return None

    def help(self):
        print('\n命令 (路径模式)')
        print(' pwd                       显示当前路径')
        print(' ls [path]                 列出目录内容')
        print(' cd <path>                 切换目录 (支持 .. / 绝对/相对)')
        print(' download <path...> [local_dir]  下载文件/目录(目录递归展开) 可加本地目标目录')
        print(' link <path>               显示下载直链')
        print(' delete <path>             删除文件或目录(目录进入回收站)')
        print(' mkdir <path>              创建目录(可递归如 a/b/c)')
        print(' setdir <local_path>       设置默认本地下载保存目录')
        print(' upload                    上传文件 (交互输入本地路径)')
        print(' share                     交互式分享(暂未重构)')
        print(' log                       重新登录刷新 token')
        print(' re                        刷新当前目录缓存')
        print(' reload                    重新读取配置文件')
        print(' help                      帮助')
        print(' exit                      退出')

# ================================= 主循环 =================================
if __name__ == '__main__':
    pan = Pan123(readfile=True, input_pwd=True)
    print('当前目录:', pan.pwd())
    if readline and not hasattr(readline, 'get_line_buffer'):
        try: pan._setup_completion()
        except: pass
    while True:
        try:
            cmd_line = input('\033[92m123pan:\033[0m' + pan.pwd() + '$ ').strip()
        except (EOFError, KeyboardInterrupt):
            print('\n退出')
            break
        if not cmd_line: continue
        parts = cmd_line.split()
        cmd = parts[0]
        args = parts[1:]
        if cmd == 'exit': break
        elif cmd == 'help': pan.help()
        elif cmd == 'pwd': print(pan.pwd())
        elif cmd == 'ls': pan.ls(args[0] if args else '')
        elif cmd == 'cd':
            if not args: print('缺少路径'); continue
            pan.cd(args[0])
        elif cmd == 're':
            pan.re()
        elif cmd == 'setdir':
            if not args: print('缺少本地路径'); continue
            pan.set_download_dir(args[0])
        elif cmd == 'link':
            if not args: print('缺少路径'); continue
            pan.link_path(args[0])
        elif cmd == 'download':
            if not args: print('缺少路径'); continue
            remote_patterns = args
            local_dir = None
            if len(args) > 1:
                last_arg = args[-1]
                # 新判定：必须已存在至少一个远程参数，并且最后一个参数不可解析为远程且本地路径存在或以 ~ 开头
                has_remote = any(('*' in a or '?' in a or pan.resolve(a) is not None) for a in args[:-1])
                if has_remote and '*' not in last_arg and '?' not in last_arg and pan.resolve(last_arg) is None and (last_arg.startswith('~') or os.path.exists(os.path.expanduser(last_arg.rstrip('/')))):
                    local_dir = os.path.expanduser(last_arg)
                    remote_patterns = args[:-1]
                    print(f"检测到本地下载路径: {local_dir}")
            expanded = []
            for p in remote_patterns:
                if '*' in p or '?' in p:
                    norm = pan.normalize(p)
                    base_dir = '/' + '/'.join(norm.strip('/').split('/')[:-1]) if '/' in norm.strip('/') else pan.pwd()
                    pattern = norm.strip('/').split('/')[-1]
                    base_node = pan.resolve(base_dir)
                    if not base_node or base_node['Type']!=1:
                        print('通配父目录不存在:', base_dir); continue
                    for ch in pan.api_list(base_node['FileId']):
                        if fnmatch(ch['FileName'], pattern):
                            child_path = (base_dir.rstrip('/')+'/' if base_dir!='/' else '/') + ch['FileName']
                            expanded.append(child_path)
                else:
                    expanded.append(p)
            final_files = []
            for ep in expanded:
                final_files.extend(pan.collect_files(ep))
            if not final_files:
                print('没有可下载文件'); continue
            print('总文件数(含目录展开):', len(final_files))
            pan.download_many_paths(final_files, local_dir)
        elif cmd == 'delete':
            if not args: print('缺少路径'); continue
            pan.delete_path(args[0])
        elif cmd == 'mkdir':
            if not args: print('缺少路径'); continue
            pan.mkdir(args[0])
        elif cmd == 'upload':
            local_path = input('本地文件路径: ').strip()
            pan.up_load(local_path)
        elif cmd == 'log': pan.login()
        elif cmd == 'reload': pan.read_ini('', '', True); print('读取成功')
        elif cmd == 'share':
            print('分享交互仍使用旧编号逻辑，暂未重构')
            pan.share()
        else:
            print('未知命令，输入 help 获取帮助')
