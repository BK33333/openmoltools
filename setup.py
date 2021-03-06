"""openmoltools: Tools for Small Molecules, Antechamber, OpenMM, and More.
"""

from __future__ import print_function
DOCLINES = __doc__.split("\n")

import os
import sys
import shutil
import tempfile
import subprocess
from distutils.ccompiler import new_compiler
from setuptools import setup, Extension

import numpy
try:
    from Cython.Distutils import build_ext
    setup_kwargs = {'cmdclass': {'build_ext': build_ext}}
    cython_extension = 'pyx'
except ImportError:
    setup_kwargs = {}
    cython_extension = 'c'

##########################
VERSION = "0.9.0dev0"
ISRELEASED = False
__version__ = VERSION
##########################


CLASSIFIERS = """\
Development Status :: 3 - Alpha
Intended Audience :: Science/Research
Intended Audience :: Developers
License :: OSI Approved :: MIT License
Programming Language :: C
Programming Language :: Python
Programming Language :: Python :: 3
Topic :: Scientific/Engineering :: Bio-Informatics
Topic :: Scientific/Engineering :: Chemistry
Operating System :: Microsoft :: Windows
Operating System :: POSIX
Operating System :: Unix
Operating System :: MacOS
"""

extensions = []

setup(name='openmoltools',
      author='Kyle A. Beauchamp',
      author_email='kyleabeauchamp@gmail.com',
      description=DOCLINES[0],
      long_description="\n".join(DOCLINES[2:]),
      version=__version__,
      license='MIT',
      url='http://github.com/choderalab/openmoltools',
      platforms=['Linux', 'Mac OS-X', 'Unix'],
      classifiers=CLASSIFIERS.splitlines(),
      packages=["openmoltools", "openmoltools.tests"],
      zip_safe=False,
      scripts=['scripts/generate_example_data.py', 'scripts/processAmberForceField.py'],
      ext_modules=extensions,
      package_data={'openmoltools': ['chemicals/*/*', 'parameters/*']},  # Install all data directories of the form testsystems/data/X/
      **setup_kwargs
      )
