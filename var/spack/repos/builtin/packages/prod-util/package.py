# Copyright 2013-2023 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

from spack.package import *


class ProdUtil(CMakePackage):
    """
    Product utilities for the NCEP models.

    This is part of NOAA's NCEPLIBS project."""

    homepage = "https://github.com/NOAA-EMC/NCEPLIBS-prod_util"
    url = "https://github.com/NOAA-EMC/NCEPLIBS-prod_util/archive/refs/tags/v1.2.2.tar.gz"
    git = "https://github.com/NOAA-EMC/NCEPLIBS-prod_util"

    maintainers("AlexanderRichert-NOAA", "Hang-Lei-NOAA", "edwardhartnett")

    version("develop", branch="develop")
    version("2.1.0", sha256="fa7df4a82dae269ffb347b9007376fb0d9979c17c4974814ea82204b12d70ea5")

    depends_on("w3emc")

    def check(self):
        with working_dir(self.builder.build_directory):
            make("test")
