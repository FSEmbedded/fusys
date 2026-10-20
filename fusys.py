#! /usr/bin/env python3

# Copyright 2026 F&S Elektronik Systeme GmbH
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of
# this software and associated documentation files (the “Software”), to deal in
# the Software without restriction, including without limitation the rights to use,
# copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the
# Software, and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
# INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
# DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
# ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
# IN THE SOFTWARE.
import argparse
import configparser
import importlib.util
from pathlib import Path
import subprocess
import shutil
import json
import os
import xml.etree.ElementTree as ET
script_dir = Path(__file__).resolve().parent
config_file=f"{script_dir}/.fusys.ini"
CONFIG = configparser.ConfigParser()
CONFIG.read(config_file)

def setup(buildir):
    if not buildir.strip():
        raise ValueError("buildir must not be empty")
    build_path = Path(buildir).expanduser().resolve()
    manifest = ET.parse(script_dir / "fs-release-manifest.xml").getroot()
    release = manifest.find("release")
    docker = manifest.find("docker")
    if release is None or docker is None:
        raise ValueError("Manifest requires release and docker elements")
    if not release.get("arch") or not release.get("version") or not docker.get("version"):
        raise ValueError("Manifest requires release arch/version and docker version")

    config = configparser.ConfigParser()
    config["release"] = {
        "name": release.get("name") or release.get("arch"),
        "arch": release.get("arch"),
        "version": release.get("version"),
        "build_dir": str(build_path),
    }
    for name, script in (("docker-fus", "fus-docker.py"),
                         ("yocto-fus", "fus-build.py"),
                         ("fs-cve-tracker", "fs-cve-tracker.py")):
        project = manifest.find(f"project[@name='{name}']")
        if project is not None:
            project_path = Path(project.get("path") or name)
        elif name == "yocto-fus":
            project = manifest.find("project[@name='meta-fus']")
            if project is None or not project.get("path"):
                raise ValueError("Manifest requires yocto-fus or meta-fus with a path")
            project_path = Path(project.get("path")).parent.parent
        else:
            raise ValueError(f"Manifest requires project {name}")
        config[name] = {"path": str(build_path / project_path), "script": script}
        if name == "docker-fus":
            config[name]["tag"] = docker.get("version")

    docker_config = configparser.ConfigParser()
    docker_config["docker-fus"] = {
        "build_dir": str(build_path),
        "tag": config["docker-fus"]["tag"],
    }
    docker_config_path = Path(config["docker-fus"]["path"]) / ".docker.ini"
    with open(docker_config_path, "w", encoding="utf-8") as output:
        docker_config.write(output, space_around_delimiters=False)

    yocto_build_config = Path(config["yocto-fus"]["path"]) / ".docker_path"
    with open(yocto_build_config, "w", encoding="utf-8") as output:
        output.write(str(Path(config["docker-fus"]["path"]) / config["docker-fus"]["script"]))

    yocto_build_config = Path(config["yocto-fus"]["path"]) / ".fusys_archs"
    with open(yocto_build_config, "w", encoding="utf-8") as output:
        output.write(config["release"]["arch"])

    with open(config_file, "w", encoding="utf-8") as output:
        config.write(output, space_around_delimiters=False)

def set_config_value(path, key, value):
    path = Path(path)
    line = f"{key} = \"{value}\"\n"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True) if path.exists() else []

    for index, existing_line in enumerate(lines):
        if existing_line.startswith(f"{key} ="):
            lines[index] = line
            break
    else:
        lines.append(line)

    path.write_text("".join(lines), encoding="utf-8")

def parseNamedArguments(parser, option, arguments, requiredKeys, optionalKeys=(), repeatableKeys=()):
    parsedArguments = {}
    allowedKeys = set(requiredKeys) | set(optionalKeys) | set(repeatableKeys)

    for argument in arguments:
        if "=" not in argument:
            parser.error(f"{option} arguments must use key=value")
        key, value = argument.split("=", 1)
        if key not in allowedKeys:
            parser.error(f"{option} does not support {key}=...")
        if key in repeatableKeys:
            parsedArguments.setdefault(key, []).append(value)
        elif key in parsedArguments:
            parser.error(f"{option} received {key}=... more than once")
        else:
            parsedArguments[key] = value

    missingKeys = set(requiredKeys) - set(parsedArguments)
    if missingKeys:
        requirements = " and ".join(f"{key}=..." for key in requiredKeys)
        parser.error(f"{option} requires {requirements}")
    return parsedArguments

class cmd_runner:
    def __init__(self, config):
        self.use_docker = False
        if config.has_section("docker-fus"):
            self.docker_path = config.get("docker-fus", "path")
            self.docker_tag = config.get("docker-fus", "tag")
            self.docker_script = config.get("docker-fus", "script")
            self.build_dir = config.get("release", "build_dir")
            self.use_docker = True

    def run(self, cmd):
        if self.use_docker:
            subprocess.run( ["bash", f"{self.docker_path}/{self.docker_script}", "-i" ,self.docker_tag, "-d", self.build_dir, "-c", cmd], check=True)
        else:
            subprocess.run(cmd, shell=True, check=True, executable="/bin/bash")

class fsbuild:
        def __init__(self, config=CONFIG):
            yoctoPath = Path(config.get("yocto-fus", "path"))
            yoctoScript = yoctoPath / config.get("yocto-fus", "script")
            moduleName = f"yoctoBuild_{abs(hash(yoctoScript))}"
            spec = importlib.util.spec_from_file_location(moduleName, yoctoScript)
            yoctoModule = importlib.util.module_from_spec(spec)
            yoctoModule.scriptDir = yoctoPath
            spec.loader.exec_module(yoctoModule)
            self.yoctoBuild = yoctoModule.fsbuild( scriptDir=yoctoPath)
            self.buildJSON=json.loads(self.yoctoBuild.getBuildsJSON())

        def get_avail_builds(self):
            return "\n".join(self.buildJSON.keys())

        def get_avail_machines(self, build):
            return "\n".join(self.buildJSON.get(build, {})["MACHINES"].keys())

        def get_avail_images(self, build, arch):
            return "\n".join(self.buildJSON.get(build, {})["MACHINES"][arch]["BUILT_IMAGES"])

        def get_sbom(self, build, arch, image):
            return str(self.buildJSON.get(build, {})["MACHINES"][arch]["BUILT_IMAGES"][image]["SBOM"])

        def get_vex(self, build, arch, image):
            return str(self.buildJSON.get(build, {})["MACHINES"][arch]["BUILT_IMAGES"][image]["VEX"])

        def get_deploy_path(self, build, arch):
            return str(self.buildJSON.get(build, {})["MACHINES"][arch]["DEPLOY_PATH"])

        def get_config_file(self, build):
            return self.buildJSON.get(build, {}).get("CONFIG_FILE", "")

        def import_from_build(self, build, arch, image, target_path):
            sbomPath = self.get_sbom(build, arch, image)
            vexPath = self.get_vex(build, arch, image)
            deployPath = self.get_deploy_path(build, arch)

            os.makedirs(target_path, exist_ok=True)
            print(f"Importing {sbomPath}")
            shutil.copyfile(sbomPath, Path(target_path) / "bom_yocto.json")
            print(f"Importing {vexPath}")
            shutil.copyfile(vexPath, Path(target_path) / "vex_yocto.json")
            spdxPath = Path(deployPath) / "recipe-linux-fus-debug.spdx.json"
            print(f"Importing {spdxPath}")
            shutil.copy2(spdxPath, target_path)

        def configure_build(self, build, image, certpath):
            certpath_docker = str(Path("/home/developer/fs-ota-update/") / certpath)
            localconf = self.buildJSON.get(build, {}).get("CONFIG_FILE", "")
            set_config_value(localconf, "FUS_UPDATE_CERT_DIR", certpath_docker)
            set_config_value(localconf, "FUS_BUNDLE_ROOTFS_IMAGE", image)

        def get_rauc_update_bundle(self, build, arch, image):
            deployPath = self.get_deploy_path(build, arch)
            return str(Path(deployPath) / f"fus-fw-bundle-{arch}.raucb")

class fusys:
    def __init__(self, config = CONFIG):
        self.config = config
        self.cmd_runner = cmd_runner(self.config)

    def get_supported_machines(self):
        return "\n".join(self.config.get("release", "arch").split())
    def get_version(self):
        return self.config.get("release", "version")
    def get_release_name(self):
        name=self.config.get("release", "name")
        if not name:
            name=self.config.get("release", "arch")
        version=self.config.get("release", "version")
        return f"{name}-{version}"
    def run_cmd(self, cmd):
        self.cmd_runner.run(cmd)
    def get_fusys_path(self):
        return Path(__file__).resolve()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="F&S development environment")
    parser.add_argument("--setup", nargs=1, metavar="build_dir=...",
                        help="Generate .fusys.ini from fs-release-manifest.xml")
    parser.add_argument("--getReleaseName", action="store_true",
                        help="Get the release name of this build")
    parser.add_argument("--supportedMachines", action="store_true",
                        help="Get supported machines of this build")
    parser.add_argument("--availableMachines", nargs = 1, metavar=("build=..."),
                        help="Get available machines of this build")
    parser.add_argument("--availableBuilds", action="store_true",
                        help="Get available builds of this release")
    parser.add_argument("--availableImages", nargs=2, metavar=("build=...", "machine=..."),
                        help="Find available images")
    parser.add_argument("--getFusysPath", action="store_true",
                        help="Get the path of the fusys script")
    parser.add_argument("--getRaucUpdateBundle", nargs=3, metavar=("build=...", "machine=...", "image=..."),
                        help="Get the path of the RAUC update bundle")
    parser.add_argument("--getConfigFile", nargs = 1, metavar=("build=..."),
                        help="Get the local configuration for the specified build")
    parser.add_argument("--printBuildsJSON", help="Print builds in JSON format", action="store_true")
    parser.add_argument("--importSBOMfiles", nargs=4, metavar=("build=...", "machine=...", "image=...", "inputDir=..."),
                        help="Import files from a specific build, architecture, and image")
    args = parser.parse_args()

    if args.setup:
        setup_args = parseNamedArguments(parser, "--setup", args.setup, ("build_dir",))
        try:
            setup(setup_args["build_dir"])
        except (OSError, ET.ParseError, ValueError) as error:
            parser.error(f"--setup: {error}")
        raise SystemExit(0)

    fusys = fusys()
    fsbuild = fsbuild()

    if args.supportedMachines:
        print (fusys.get_supported_machines())
    if args.getReleaseName:
        print(fusys.get_release_name())
    if args.availableMachines:
        avail_machines_args = parseNamedArguments(parser, "--availableMachines", args.availableMachines, ("build",))
        print (fsbuild.get_avail_machines(avail_machines_args["build"]))
    if args.printBuildsJSON:
        fsbuild.yoctoBuild.printBuildsJSON()
    if args.getRaucUpdateBundle:
        rauc_args = parseNamedArguments(parser, "--getRaucUpdateBundle", args.getRaucUpdateBundle, ("build", "machine", "image"))
        print(fsbuild.get_rauc_update_bundle(rauc_args["build"], rauc_args["machine"], rauc_args["image"]))
    if args.availableBuilds:
        print(fsbuild.get_avail_builds())
    if args.availableImages:
        images_args = parseNamedArguments(parser, "--availableImages", args.availableImages, ("build", "machine"))
        if not images_args["machine"] or images_args["machine"] == "":
            print("")
            exit(0)
        print(fsbuild.get_avail_images(images_args["build"], images_args["machine"]))
    if args.getFusysPath:
        print(fusys.get_fusys_path())
    if args.getConfigFile:
        config_args = parseNamedArguments(parser, "--getConfigFile", args.getConfigFile, ("build",))
        print(fsbuild.get_config_file(config_args["build"]))
    if args.importSBOMfiles:
        import_args = parseNamedArguments(parser, "--importSBOMfiles", args.importSBOMfiles, ("build", "machine", "image"), ("inputDir",))
        fsbuild.import_from_build(import_args["build"], import_args["machine"], import_args["image"], import_args["inputDir"])


