from setuptools import setup, find_packages

setup(
    name="emu-stitch",
    version="1.0.0",
    author="R. F. Hansen-Smith",
    author_email="opensource@emu-stitch.org",
    description="Open-Source Multi-User Emulator Save Synchronizer & Profile Switcher for Handheld Consoles",
    long_description=open("README.md").read() if os.path.exists("README.md") else "",
    long_description_content_type="text/markdown",
    url="https://github.com/R-F-Hansen-Smith/emu-stitch",
    packages=find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: POSIX :: Linux",
    ],
    python_requires=">=3.8",
    entry_points={
        "console_scripts": [
            "emu-stitch=emu_stitch.cli:main",
        ],
    },
)
