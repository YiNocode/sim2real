# Sim2Real 合作开发指南

`main` 是项目的集成分支。合作者在自己的电脑上开发机型档案和飞行日志工作台，从 `main` 新建分支，测试后提交 Pull Request（PR）。仓库维护者审核、决定是否合并，并负责把合并后的代码部署到实验室服务器。

## 获取访问权限

维护者在私有仓库的 **Settings → Collaborators → Add people** 中按 GitHub 用户名邀请合作者。合作者接受邀请后，在自己的电脑上克隆仓库。GitHub 仓库权限和实验室服务器的 SSH 权限由维护者分别配置。

```bash
git clone https://github.com/YiNocode/sim2real.git
cd sim2real
git switch main
git pull --ff-only origin main
git switch -c feature/workbench-profile-form
```

每项独立修改从最新的 `main` 创建一个分支。分支名示例 `feature/workbench-profile-form`、`fix/flight-upload-validation`。

## 本地启动工作台

需要 Git 和 Python 3.10 或更新版本。工作台开发和下面的单元测试可在本地运行。

**Windows PowerShell**

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m flask --app sim2real.web:create_app run --port 8000
```

**Linux / macOS**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m flask --app sim2real.web:create_app run --port 8000
```

在浏览器打开 `http://127.0.0.1:8000/`。本地机型和日志默认存放在项目的 `workspace/`。可导入 `profiles/example.yaml`，或从页面加载示例。需要验证日志上传时，使用模拟或脱敏测试文件，在本地工作台操作。

| 修改内容 | 入口 |
| --- | --- |
| 工作台页面、交互和样式 | `web/index.html`、`web/app.js`、`web/style.css` |
| 档案与日志 API、上传和下载 | `sim2real/web.py` |
| 机型字段、校验和示例 | `sim2real/profile.py`、`profiles/example.yaml` |
| 单元测试 | `tests/test_sim2real.py` |

调整表单字段时，检查前后端字段名、单位和校验是否一致，并试读既有版本档案。修改飞行日志页面时，保留 VIO 或光流加测距的实际定位来源、标定或验收用途，以及机型版本关联。没有独立参考轨迹的记录应继续标明位置来自 PX4 估计。完整实验和部署说明见 [README](README.md)。

## 提交和更新 PR

1. 在虚拟环境中运行 `python -m unittest discover -s tests -v`。也可把命令里的 `python` 换成上述虚拟环境的解释器路径。改工作台时，按涉及的流程手动复测示例导入、档案保存及重开、重复版本提示、日志上传和下载，并确认无效输入有清晰提示。
2. 检查 `git status --short` 和 `git diff`。用 `git add -- <文件路径>` 逐项暂存，再用 `git diff --cached` 确认提交范围。
3. 提交并推送自己的分支。

   ```bash
   git commit -m "Improve airframe profile validation"
   git push -u origin feature/workbench-profile-form
   ```

4. 在 GitHub 创建 PR，目标分支（base）选 `main`，来源分支（compare）选自己的分支，请维护者审核。正文写明修改目的、验证命令和结果、手动复现步骤。页面改动可附脱敏截图。涉及字段或存储格式时，说明已有档案与日志如何读取。
5. 根据审核意见，在同一分支提交并推送更新。需要同步主分支时，在自己的分支运行 `git fetch origin` 和 `git merge origin/main`，解决冲突、复测后推送。已有 PR 会随分支更新。

## 维护者审核、合并和部署

- 审核 GitHub **Files changed** 与测试结果。重点看字段单位、旧档案兼容性、上传错误提示和日志版本关联。涉及控制或估计接口时，在合并前明确相应的验证证据。
- 审核通过后由维护者执行合并。合作者的日常改动通过 PR 进入 `main`。如果仓库支持受保护分支或规则集，维护者可给 `main` 启用 PR 及至少一次审核要求。
- 服务器更新由维护者单独执行。先确认部署工作目录和数据目录，再更新到已审核的提交，验证工作台和既有记录可读。维护者记录所部署的提交 SHA。前端开发使用本地实例。

源码仓库保存代码、文档和合成示例。真实机型档案、PX4 参数快照、ULog、指令和定位记录请放到授权的实验室数据目录。`workspace/`、`build/`、凭据和策略产物应留在 Git 之外。PR 截图和测试附件也遵循同样的数据边界。
