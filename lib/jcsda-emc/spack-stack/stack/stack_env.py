import copy
import io
import logging
import os
import re
import shutil
import subprocess

import spack
import spack.config
import spack.environment as ev
import spack.util.spack_yaml as syaml
from spack.extensions.stack.stack_paths import common_path, site_path, stack_path, template_path

default_manifest_yaml = """\
# This is a Spack Environment file.
#
# It describes a set of packages to be installed, along with
# configuration settings.
# Includes are in order of highest precedence first.
# Site configs take precedence over the base packages.yaml.
spack:

  view: false

"""

# Hidden file in top-level spack-stack dir so this module can
# find relative config files. Assuming Spack is a submodule of
# spack-stack.
check_file = ".spackstack"


def get_git_revision_short_hash(path) -> str:
    return (
        subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=path)
        .decode("ascii")
        .strip()
    )


def stack_hash():
    return get_git_revision_short_hash(stack_path())


def spack_hash():
    return get_git_revision_short_hash(spack.paths.spack_root)


class StackEnv(object):
    """Represents a spack.yaml environment based on different
    configurations of sites and specs. Uses the Spack library
    to maintain an internal state that represents the yaml and
    can be written out with write(). The output is a pure
    Spack environment.
    """

    def __init__(self, **kwargs):
        """
        Construct properties directly from kwargs so they can
        be passed in through a dictionary (input file), or named
        args for command-line usage.
        """

        self.dir = kwargs.get("dir")
        self.template = kwargs.get("template", None)
        self.name = kwargs.get("name")

        self.includes = []

        # Config can be either name in apps dir or an absolute path to
        # to a spack.yaml to be used as a template. If None then empty
        # template is used.
        if not self.template:
            self.env_yaml = syaml.load_config(default_manifest_yaml)
            self.template_path = None
        else:
            if os.path.isabs(self.template):
                self.template_path = self.template
                template = self.template
            elif os.path.exists(os.path.join(template_path, self.template)):
                self.template_path = os.path.join(template_path, self.template)
                template = os.path.join(self.template_path, "spack.yaml")
            else:
                raise Exception('Template: "{}" does not exist'.format(self.template))

            with open(template, "r") as f:
                self.env_yaml = syaml.load_config(f)

        self.site = kwargs.get("site", None)
        if self.site == "none":
            self.site = None
        self.desc = kwargs.get("desc", None)
        self.compiler = kwargs.get("compiler", None)
        self.mpi = kwargs.get("mpi", None)
        self.base_packages = kwargs.get("base_packages", None)
        self.install_prefix = kwargs.get("install_prefix", None)
        self.mirror = kwargs.get("mirror", None)
        self.upstreams = kwargs.get("upstreams", None)
        self.modulesys = kwargs.get("modulesys", None)
        self.modifypkg = kwargs.get("modifypkg", None)

        if not self.name:
            # site = self.site if self.site else 'default'
            self.name = "{}.{}".format(self.template, self.site)

    def env_dir(self):
        """env_dir is <dir>/<name>"""
        return os.path.join(self.dir, self.name)

    def add_includes(self, includes):
        self.includes.extend(includes)

    def get_lmod_or_tcl(self, site_configs_dir):
        site_modules_yaml_path = os.path.join(site_configs_dir, "modules.yaml")
        with open(site_modules_yaml_path, "r") as f:
            site_modules_yaml = syaml.load_config(f)
        lmod_or_tcl_list = site_modules_yaml["modules"]["default"]["enable"]
        assert lmod_or_tcl_list and (
            set(lmod_or_tcl_list) in ({"tcl"}, {"lmod"})
        ), """Set one and only one value ('lmod' or 'tcl') under 'modules:default:enable'
           in site modules.yaml, or use '--modulesys {tcl,lmod}'"""
        return lmod_or_tcl_list[0]

    def _copy_common_includes(self):
        """Copy common directory into environment"""
        self.includes.append("common")
        env_common_dir = os.path.join(self.env_dir(), "common")
        shutil.copytree(
            common_path, env_common_dir, ignore=shutil.ignore_patterns("modules_*.yaml")
        )
        if self.modulesys:
            lmod_or_tcl = self.modulesys
        else:
            lmod_or_tcl = self.get_lmod_or_tcl(self.site_configs_dir())
        common_modules_yaml_path = os.path.join(common_path, "modules_%s.yaml" % lmod_or_tcl)
        destination = os.path.join(env_common_dir, "modules.yaml")
        shutil.copy(common_modules_yaml_path, destination)

    def site_configs_dir(self):
        site_configs_dir = os.path.join(site_path, self.site)
        return site_configs_dir

    def _copy_site_includes(self):
        """Copy site directory into environment"""
        if not self.site:
            raise Exception("Site is not set")

        site_name = "site"
        self.includes.append(site_name)
        env_site_dir = os.path.join(self.env_dir(), site_name)
        shutil.copytree(self.site_configs_dir(), env_site_dir)
        # Update site modules.yaml if user overrides default module system
        if not self.modulesys:
            return
        lmod_or_tcl = self.modulesys
        site_modules_yaml_path = os.path.join(env_site_dir, "modules.yaml")
        with open(site_modules_yaml_path, "r") as f:
            site_modules_yaml = syaml.load_config(f)
        current_sys = site_modules_yaml["modules"]["default"]["enable"][0]
        if lmod_or_tcl == current_sys:
            return
        logging.info("Updating site modules.yaml to reflect env module system override setting\n")
        site_modules_yaml["modules"]["default"]["enable"][0] = lmod_or_tcl
        current_config = site_modules_yaml["modules"]["default"][current_sys]
        site_modules_yaml["modules"]["default"][lmod_or_tcl] = current_config
        del site_modules_yaml["modules"]["default"][current_sys]
        # Write file, and get rid of annoying single quotes
        stream = io.StringIO()
        syaml.dump_config(site_modules_yaml, stream)
        with open(site_modules_yaml_path, "w") as f:
            f.write(stream.getvalue().replace("'enable:':", "enable::"))

    def _copy_package_includes(self):
        """Overwrite base packages in environment common dir"""
        env_common_dir = os.path.join(self.env_dir(), "common")
        shutil.copy(self.base_packages, env_common_dir)

    def write(self):
        """Write environment out to a spack.yaml in <env_dir>/<name>.
        Will create env_dir if it does not exist.
        """
        env_dir = self.env_dir()
        env_yaml = self.env_yaml

        if os.path.exists(env_dir):
            raise Exception("Environment '{}' already exists.".format(env_dir))

        os.makedirs(env_dir, exist_ok=True)

        # Copy site config first so that it takes precedence in env
        if self.site != "none":
            self._copy_site_includes()

        # Copy common include files
        self._copy_common_includes()

        # Overwrite common packages if base_packages is not None
        if self.base_packages:
            self._copy_package_includes()

        # No way to add to env includes using pure Spack.
        env_yaml["spack"]["include"] = self.includes

        # Write out file with includes filled in.
        env_file = os.path.join(env_dir, "spack.yaml")
        with open(env_file, "w") as f:
            # Write header with hashes.
            header = "spack-stack hash: {}\nspack hash: {}"
            env_yaml.yaml_set_start_comment(header.format(stack_hash(), spack_hash()))
            syaml.dump_config(env_yaml, stream=f)

        # Activate environment
        env = ev.Environment(manifest_dir=env_dir)
        ev.activate(env)
        env_scope = env.env_file_config_scope_name()

        # Save original data in spack.yaml because it has higest precedence.
        # spack.config.add will overwrite as it goes.
        # Precedence order (high to low) is original spack.yaml,
        # then common configs, then site configs.
        original_sections = {}
        for key in spack.config.SECTION_SCHEMAS.keys():
            section = spack.config.get(key, scope=env_scope)
            if section:
                original_sections[key] = copy.deepcopy(section)

        # Commonly used config settings
        if self.compiler:
            compiler = "packages:all::compiler:[{}]".format(self.compiler)
            spack.config.add(compiler, scope=env_scope)
        if self.mpi:
            mpi = "packages:all::providers:mpi:[{}]".format(self.mpi)
            spack.config.add(mpi, scope=env_scope)
        if self.install_prefix:
            # Modules can go in <prefix>/modulefiles by default
            prefix = "config:install_tree:root:{}".format(self.install_prefix)
            spack.config.add(prefix, scope=env_scope)
            module_prefix = os.path.join(self.install_prefix, "modulefiles")
            lmod_prefix = "modules:default:roots:lmod:{}".format(module_prefix)
            tcl_prefix = "modules:default:roots:tcl:{}".format(module_prefix)
            spack.config.add(lmod_prefix, scope=env_scope)
            spack.config.add(tcl_prefix, scope=env_scope)
        if self.upstreams:
            for upstream_path in self.upstreams:
                upstream_path = upstream_path[0]
                # spack doesn't handle "~/" correctly, this fixes it:
                upstream_path = os.path.expanduser(upstream_path)
                if not os.path.basename(os.path.normpath(upstream_path)) == "install":
                    logging.warning(
                        "WARNING: Upstream path '%s' is not an 'install' directory!"
                        % upstream_path
                    )
                if not os.path.isdir(upstream_path):
                    logging.warning(
                        "WARNING: Upstream path '%s' does not appear to exist!" % upstream_path
                    )
                re_pattern = ".+/(?P<spack_stack_ver>spack-stack-[^/]+)/envs/(?P<env_name>[^/]+)"
                path_parts = re.match(re_pattern, upstream_path)
                if path_parts:
                    name = path_parts["spack_stack_ver"] + "-" + path_parts["env_name"]
                else:
                    name = os.path.basename(upstream_path)
                upstream = "upstreams:%s:install_tree:'%s'" % (name, upstream_path)
                logging.info("Adding upstream path '%s'" % upstream_path)
                spack.config.add(upstream, scope=env_scope)
        if self.modifypkg:
            logging.info("Creating custom repo with packages %s" % ", ".join(self.modifypkg))
            env_repo_path = os.path.join(env_dir, "envrepo")
            env_pkgs_path = os.path.join(env_repo_path, "packages")
            os.makedirs(env_pkgs_path, exist_ok=False)
            with open(os.path.join(env_repo_path, "repo.yaml"), "w") as f:
                f.write("repo:\n  namespace: envrepo")
            repo_paths = spack.config.get("repos", scope=spack.config.default_list_scope())
            repo_paths = [p.replace("$spack/", spack.paths.spack_root + "/") for p in repo_paths]
            for pkg_name in self.modifypkg:
                pkg_found = False
                for repo_path in repo_paths:
                    pkg_path = os.path.join(repo_path, "packages", pkg_name)
                    if os.path.exists(pkg_path):
                        shutil.copytree(
                            pkg_path,
                            os.path.join(env_pkgs_path, pkg_name),
                            ignore=shutil.ignore_patterns("__pycache__"),
                        )
                        pkg_found = True
                        # Use the first repo where the package exists:
                        break
                if not pkg_found:
                    logging.warning(f"WARNING: package '{pkg_name}' could not be found")
            logging.info("Adding custom repo 'envrepo' to env config")
            repo_cfg = "repos:[$env/envrepo]"
            spack.config.add(repo_cfg, scope=env_scope)

        # Merge the original spack.yaml template back in
        # so it has the higest precedence
        for section in spack.config.SECTION_SCHEMAS.keys():
            original = original_sections.get(section, {})
            existing = spack.config.get(section, scope=env_scope)
            new = spack.config.merge_yaml(existing, original)
            if section in existing:
                spack.config.set(section, new[section], env_scope)

        with env.write_transaction():
            env.write()

        ev.deactivate()

        logging.info("Successfully wrote environment at {}".format(env_file))

    def check_umask(self):
        """Check if the user's umask will create environments that are not readable
        by group or others and if so, issue a warning.
        """

        # os.mask requires a new value for the mask and returns the old mask.
        # The new value doesn't matter, since it is forgotten when the Python
        # script terminates. See https://www.geeksforgeeks.org/python-os-umask-method/
        newmask = 18  # decimal, same as 0o022 in octal
        oldmask = os.umask(newmask)
        if oldmask == 18:
            # 18 = 0o022
            logging.info("\nChecked user umask and found no issues (0022)\n")
        elif oldmask == 23:
            # 23 = 0o027
            logging.warning(
                "\nWARNING! User umask only allows owner and group to read the env (0027)\n"
            )
        else:
            logging.warning(
                "\nWARNING! User umask is neither 0022 nor 0027, check before proceeding\n"
            )
