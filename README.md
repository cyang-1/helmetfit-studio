# 量身造盔 · HelmetFit Studio

面向 Cosplay 玩家与道具制作工作室的个性化头盔定制工作台。用文字、单图或多视图调用 Aholo Lux3D G1 生成头盔外形，在三维视窗中按头围调整佩戴空间，导出试戴环和整盔的 Bambu Lab P1S 打印工程。

[在线体验](https://modelscope.ai/studios/CYAng01/helmetfit-studio) · [项目报告书](release/参赛提交/量身造盔-项目报告书.pdf) · [工作台 Demo](release/参赛提交/量身造盔-Demo.mp4)

## 使用

- **Windows 本机**：安装 Blender 4.5 和 Bambu Studio，配置自己的 Aholo API Key，双击 `start-studio.bat`。[本地使用说明](STUDIO.md)
- **Docker 部署**：使用仓库根目录的 Dockerfile。容器集成 Blender 和 Bambu Studio，监听 7860 端口，按浏览器会话隔离作品。[部署说明](deploy/modelscope/README.md)

## 源码

`studio/` 包含生成任务、网格修复、佩戴适配和切片服务；`static/studio/` 包含三维工作台；`tests/` 和 `tools/` 包含回归测试、端到端验证与打包脚本。试戴环与整盔的 3MF 可在 Bambu Studio 中查看模型和各层刀路。
