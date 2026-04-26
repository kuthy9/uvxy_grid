FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 安装 tzdata — zoneinfo.ZoneInfo("America/New_York") 必需, slim 镜像默认不带
# TZ 对齐代码里的 ET (纽约市场时区)
RUN apt-get update && \
    apt-get install -y --no-install-recommends tzdata ca-certificates && \
    ln -snf /usr/share/zoneinfo/America/New_York /etc/localtime && \
    echo "America/New_York" > /etc/timezone && \
    rm -rf /var/lib/apt/lists/*
ENV TZ=America/New_York

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

# 运行时产物目录 (实盘 DB / 日志 / 周报 / 数据缓存 / 状态快照都在此)
RUN mkdir -p /app/runtime /app/runtime/reports /app/runtime/data_cache

CMD ["python", "-u", "main.py"]
