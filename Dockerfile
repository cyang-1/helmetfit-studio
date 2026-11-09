FROM ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852

COPY deploy/modelscope/install-system.sh deploy/modelscope/install-engines.sh /tmp/helmetfit-build/
RUN sh /tmp/helmetfit-build/install-system.sh
ARG BLENDER_URL
ARG BAMBU_URL
RUN sh /tmp/helmetfit-build/install-engines.sh && rm -rf /tmp/helmetfit-build
COPY deploy/modelscope/bambu-cli.sh /usr/local/bin/helmetfit-bambu
RUN chmod 755 /usr/local/bin/helmetfit-bambu && \
    install -d -m 0755 -o 1000 -g 1000 /home/helmetfit /mnt/workspace

ENV HOME=/home/helmetfit \
    LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    BLENDER_EXE=/opt/blender/blender \
    BAMBU_STUDIO_EXE=/usr/local/bin/helmetfit-bambu \
    BAMBU_BBL_PROFILES=/opt/bambu-bbl-profiles \
    HELMETFIT_CLOUD_DATA=/mnt/workspace/helmetfit
WORKDIR /app
COPY fit_mesh.py ./
COPY studio/*.py ./studio/
COPY static/ ./static/
COPY deploy/modelscope/smoke.py ./deploy/modelscope/smoke.py
USER 1000:1000
RUN python3 deploy/modelscope/smoke.py
EXPOSE 7860
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "-m", "studio.cloud", "--port", "7860"]
