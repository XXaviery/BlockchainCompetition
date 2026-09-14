from setuptools import setup
from glob import glob


package_name = "mof_esp32_bridge"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
        (f"share/{package_name}/config", glob("config/*.yaml")),
        (f"share/{package_name}/launch", glob("launch/*.launch.py")),
        (f"share/{package_name}/udev", glob("udev/*.rules")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="MOF Robot",
    maintainer_email="pi@localhost",
    description="ROS2 cmd_vel to MOF ESP32 serial velocity bridge.",
    license="MIT",
    entry_points={
        "console_scripts": [
            "esp32_cmd_vel_bridge = mof_esp32_bridge.esp32_cmd_vel_bridge:main",
            "send_velocity_frame = mof_esp32_bridge.send_velocity_frame:main",
            "wasd_teleop = mof_esp32_bridge.wasd_teleop:main",
            "imu_bias_corrector = mof_esp32_bridge.imu_bias_corrector:main",
            "relative_navigation_node = mof_esp32_bridge.relative_navigation_node:main",
        ],
    },
)
