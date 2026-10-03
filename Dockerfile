FROM ros:jazzy

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# 1. 安裝系統常用工具、編譯工具與 GUI / OpenGL 相關支援
RUN apt-get update && apt-get install -y \
    curl \
    git \
    wget \
    tmux \
    nano \
    python3-pip \
    python3-colcon-common-extensions \
    python3-vcstool \
    ros-dev-tools \
    mesa-utils \
    libgl1-mesa-dri \
    && rm -rf /var/lib/apt/lists/*

# 2. 安裝 Open-RMF 核心套件、Gazebo (Harmonic) 與視覺化工具
RUN apt-get update && apt-get install -y \
    ros-jazzy-rmf-dev \
    ros-jazzy-rmf-visualization \
    ros-jazzy-rmf-building-map-tools \
    ros-jazzy-rmf-traffic-editor \
    ros-jazzy-ros-gz \
    && rm -rf /var/lib/apt/lists/*

# 3. 安裝 Python 相關依賴 (Web 控制台與任務派發工具所需)
RUN pip3 install --no-cache-dir --break-system-packages --ignore-installed \
    flask-socketio \
    fastapi \
    uvicorn \
    websockets \
    pyproj \
    pydantic

# 4. 建立 RMF 工作空間並下載編譯 rmf_demos (Jazzy 分支)
WORKDIR /root/rmf_ws
RUN mkdir -p src && cd src && \
    git clone https://github.com/open-rmf/rmf_demos.git -b jazzy

# 初始化 rosdep 並編譯 rmf_demos (略過模型下載以加速建置)
RUN . /opt/ros/jazzy/setup.sh && \
    apt-get update && \
    ([ -f /etc/ros/rosdep/sources.list.d/20-default.list ] || rosdep init) && \
    rosdep update && \
    rosdep install --from-paths src --ignore-src --rosdistro jazzy -y && \
    colcon build --cmake-args -DNO_DOWNLOAD_MODELS=On && \
    rm -rf /var/lib/apt/lists/*

# 5. 設定 bashrc 自動載入環境
RUN echo "source /opt/ros/jazzy/setup.bash" >> /root/.bashrc && \
    echo "source /root/rmf_ws/install/setup.bash" >> /root/.bashrc

WORKDIR /root/rmf_ws

ENTRYPOINT ["/ros_entrypoint.sh"]
CMD ["bash"]
