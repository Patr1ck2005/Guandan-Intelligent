# Actual public-version capture / 公开版本实际截图

![Guandan opening table](gameplay-public-baseline.png)

- Source / 源码: [`88bdabc59f1b3d16739e7270cbaa9c94ee139da4`](https://github.com/Patr1ck2005/Guandan-Intelligent/tree/88bdabc59f1b3d16739e7270cbaa9c94ee139da4)
- Capture / 拍摄: 2026-10-17, Chromium 148.0.7778.96, 1440 × 1000 viewport, device scale 1.
- Image SHA-256: `eb032da47c4dcab2ded3a79a257cb383336e899bfeb9201a8374972560fbda5c`
- [Machine-readable provenance / 机器可读来源](capture.json)

The unchanged public Git revision was exported to an isolated directory. This is the original running interface, with no restyled DOM, compositing, annotation or image editing. Only the test server was started and stopped; existing user services were not changed. There were no browser page errors. This single capture does not establish complete gameplay, AI strength, numerical accuracy or manual acceptance.

从公开 Git 版本导出隔离副本，截图来自原界面，未重写 DOM、拼接、加标注或编辑图像。只启停本次测试服务器，原有用户服务保持。页面无脚本错误；单张实录不代表完整玩法、AI 强度、数值准确度或人工验收。

## Reproduce / 最短复现

Use the linked public revision and its declared dependencies. The generated deal or browser text rendering can vary by environment; the hash identifies this capture, not a promise of pixel-identical output on every machine.

使用上方公开版本及其声明依赖。随机发牌及字体可能随环境变化；哈希用于识别本张原图，不保证跨机器像素相同。

```powershell
python -m pip install -r requirements.txt
$env:GUANDAN_PORT='18765'
$env:GUANDAN_NO_BROWSER='1'
python -m guandan.ui.web.app
```

Open `http://127.0.0.1:18765/game`. Set the global and all individual agent overrides to **Blind**, then start a game. The captured opening has the original debug hand visibility enabled. Cards are program-generated examples, not personal data. No API key is required or used.

打开上述地址，在 AI 设置中将全局及所有个人覆盖设为 **Blind 盲评**，然后开始游戏。图中保留原调试牌面显示。卡牌来自程序发牌，不含私人数据，不需要或使用 API key。
