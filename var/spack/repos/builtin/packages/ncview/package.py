# Copyright 2013-2023 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import subprocess

from spack.package import *


class Ncview(AutotoolsPackage):
    """Simple viewer for NetCDF files."""

    homepage = "https://cirrus.ucsd.edu/ncview/"

    version("2.1.9", sha256="e2317ac094af62f0adcf68421d70658209436aae344640959ec8975a645891af")
    version("2.1.8", sha256="e8badc507b9b774801288d1c2d59eb79ab31b004df4858d0674ed0d87dfc91be")
    version("2.1.7", sha256="a14c2dddac0fc78dad9e4e7e35e2119562589738f4ded55ff6e0eca04d682c82")

    depends_on("netcdf-c")
    depends_on("udunits")
    depends_on("libpng")
    depends_on("libxaw")

    def configure_args(self):
        spec = self.spec

        config_args = []

        # Problems on some systems (e.g. NASA Discover with Intel)
        # to find udunits include and library files despite
        # dependency being specified above
        config_args.append("--with-udunits2_incdir={}".format(spec["udunits"].prefix.include))
        config_args.append("--with-udunits2_libdir={}".format(spec["udunits"].prefix.lib))

        # Use the same C compiler that was used for netcdf-c
        cc = subprocess.check_output(["nc-config", "--cc"]).decode().rstrip("\n")
        config_args.append("CC={}".format(cc))

        return config_args

    def patch(self):
        # Disable the netcdf-c compiler check, save and restore the
        # modification timestamp of the file to prevent autoreconf.
        patched_file = "configure"
        with keep_modification_time(patched_file):
            filter_file(
                "if test x\$CC_TEST_SAME != x\$NETCDF_CC_TEST_SAME; then",  # noqa: W605
                "if false; then",
                patched_file,
            )

    def url_for_version(self, version):
        if version >= Version("2.1.9"):
            return "https://cirrus.ucsd.edu/~pierce/ncview/ncview-2.1.9.tar.gz"
        else:
            return "ftp://cirrus.ucsd.edu/pub/ncview/ncview-2.1.7.tar.gz"
