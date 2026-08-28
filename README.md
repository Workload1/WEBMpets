# WEBMpets
根据WEBM文件生成codex宠物

## 使用

1. 从 GitHub Releases 下载 Windows 压缩包并解压。
2. 准备包含标准动作 WEBM 或 PNG/WebP 位图的目录。
3. 双击 `WEBMpets.exe`，选择 `bitmapdir` 和 `petdir`。
4. 点击“生成桌宠”，完成后在 Codex 页面刷新并选择新宠物。

`petdir` 默认是 `~\\.codex\\pets\\`。如果它的最后一级目录名为 `pets`，程序会
自动追加 `bitmapdir` 的最后一级目录名。例如 `E:\\live2d\\myrtle` 会生成到
`~\\.codex\\pets\\myrtle`。路径也支持手动指定，并会记忆上次使用的目录。

## 源码结构

- `webm_pets_gui.py`：Tkinter 图形界面。
- `prts-spine-pet/scripts/build_bitmap_pet.py`：V1 图集生成器。
- `prts-spine-pet/scripts/process_webm.py`：WEBM 抽帧与去背景。
- `requirements.txt`：Pillow、NumPy、PyAV 依赖。

源码运行：

```powershell
python -m pip install -r requirements.txt
python webm_pets_gui.py
```

## 许可与素材

本项目脚本代码与游戏动画素材的版权相互独立。请只处理你有权使用的素材，
并遵守 PRTS Wiki、游戏及相关资源的使用条款；不要公开再分发未经授权的游戏素材。
