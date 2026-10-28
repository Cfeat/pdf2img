# PDF2Img

带可视化界面的本地 PDF 转图片工具，支持 PNG / JPG / JPEG，并可按目标存储大小自动压缩。

## 功能

- 拖拽或选择 PDF，页面预览与翻页
- 输出格式：PNG、JPG、JPEG
- 可调 DPI、页码范围、JPEG 质量
- **限制每页文件大小（KB）**：JPEG 通过质量二分搜索；PNG 通过压缩与缩放逼近目标体积
- 一键导出 ZIP（含 `manifest.csv` 体积清单）
- 纯本地处理，不上传公网

## 环境

- Python 3.10+
- Windows / macOS / Linux

## 安装与启动

```bash
cd pdf2img
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
# source .venv/bin/activate

pip install -r requirements.txt
python run.py
```

浏览器打开：<http://127.0.0.1:8765>

## 使用说明

1. 上传 PDF，右侧可预览各页  
2. 选择格式与 DPI；需要控体积时打开「限制每页文件大小」  
3. 「估算体积」查看每页大约多少 KB；「转换并下载 ZIP」导出结果  

> 目标体积过小时，工具会降低质量或缩小分辨率，图片清晰度会下降，属预期行为。
