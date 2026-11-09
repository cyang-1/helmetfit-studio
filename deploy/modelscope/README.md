# HelmetFit Studio 的 ModelScope Docker 运行环境

创空间选择 **Docker、Linux/amd64、CPU**。仓库根目录的 Dockerfile 会启动完整工作台，监听 `0.0.0.0:7860`。构建会从官方源下载 Blender 4.5.3 和 Bambu Studio 02.07.00.55，并验证两份 Linux 发行文件的 SHA-256。两个原生程序和 P1S 工艺预设的路径由镜像设定。Python 服务使用 Ubuntu 24.04 自带的 Python 3.12。

- 在创空间的密钥设置中添加 **AHOLO_API_KEY**。密钥所属区域通过 **AHOLO_REGION** 设置为 `cn` 或 `com`。不要将密钥文件放入仓库。离线导入模型与本地样例可在没有云密钥时使用。
- 将持久存储挂载到 **/mnt/workspace**。运行时 UID/GID 为 `1000:1000`，挂载点须允许该身份创建 `helmetfit` 子目录。项目数据库和文件使用 `HELMETFIT_CLOUD_DATA=/mnt/workspace/helmetfit`，在服务启动后写入。
- 运行域名由 HELMETFIT_PUBLIC_ORIGIN 指定。浏览器使用会话 Cookie 区分作品。如果嵌入窗口阻止保存 Cookie，可直接在新标签页打开运行域名。
- 本站新增 Lux3D 生成请求默认每天最多 12 次，同一浏览器会话每天最多 3 次，按 UTC 日期计数。相应配置为 HELMETFIT_DAILY_GENERATIONS 与 HELMETFIT_SESSION_DAILY_GENERATIONS；任意一项为 0 即暂停新云生成，已有模型仍可适配和下载。
- 保持单实例。Blender 几何处理与 Bambu 切片由服务排队执行。本次评审空间使用免费 CPU 规格（2 vCPU、16 GB 内存）。切片使用 Xvfb 和 Mesa 软件渲染，无需显卡或访问真实打印机。
- 镜像可通过 Docker 的构建参数 `BLENDER_URL` 和 `BAMBU_URL` 指向经团队验证的同一发行文件镜像。SHA-256 固定在安装脚本中。

从源码根目录进行本机验证：

```sh
docker build --platform linux/amd64 -t helmetfit-studio .
docker run --rm --platform linux/amd64 helmetfit-studio python3 deploy/modelscope/smoke.py --helmet
docker run --rm --platform linux/amd64 -p 7860:7860 \
  -v helmetfit-data:/mnt/workspace -e AHOLO_API_KEY -e AHOLO_REGION helmetfit-studio
```

容器构建以最终运行用户生成一个 20 mm 校准件，用 Blender 输出 GLB/STL，再用 P1S 原生配置生成并独立检查包含模型、刀路、层数和耗材的 3MF。可选的 `--helmet` 使用内置原创头盔，实际加工并切片试戴环及整盔，耗时取决于 CPU。若要保存所有产物，将可写目录挂载到容器，并传入 `--output /挂载路径/子目录`。

[Blender 4.5.3 官方校验文件](https://download.blender.org/release/Blender4.5/blender-4.5.3.sha256) · [Bambu Studio 官方发行资产](https://github.com/bambulab/BambuStudio/releases/expanded_assets/v02.07.00.55) · [Bambu Studio 命令行说明](https://github.com/bambulab/BambuStudio/wiki/Command-Line-Usage)
