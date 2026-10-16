import os
import sys
from setuptools import setup, find_namespace_packages
from fnmatch import fnmatchcase
from distutils.util import convert_path

standard_exclude = ('*.pyc', '*~', '.*', '*.bak', '*.swp*')
standard_exclude_directories = ('.*', 'CVS', '_darcs', './build', './dist', 'EGG-INFO', '*.egg-info')

def find_package_data(where='.', package='', exclude=standard_exclude, exclude_directories=standard_exclude_directories):
    out = {}
    stack = [(convert_path(where), '', package)]
    while stack:
        where, prefix, package = stack.pop(0)
        for name in os.listdir(where):
            fn = os.path.join(where, name)
            if os.path.isdir(fn):
                bad_name = False
                for pattern in exclude_directories:
                    if (fnmatchcase(name, pattern)
                        or fn.lower() == pattern.lower()):
                        bad_name = True
                        break
                if bad_name:
                    continue
                if os.path.isfile(os.path.join(fn, '__init__.py')):
                    if not package:
                        new_package = name
                    else:
                        new_package = package + '.' + name
                        stack.append((fn, '', new_package))
                else:
                    stack.append((fn, prefix + name + '/', package))
            else:
                bad_name = False
                for pattern in exclude:
                    if (fnmatchcase(name, pattern)
                        or fn.lower() == pattern.lower()):
                        bad_name = True
                        break
                if bad_name:
                    continue
                out.setdefault(package, []).append(prefix+name)
    return out

setup(name='docassemble.LITEfile',
      version='0.1.0',
      description=('Reusable Docassemble interview support for handing court documents to LITEfile.'),
      long_description='# docassemble.LITEfile\n\nReusable Docassemble interview support for handing court documents to LITEfile.\n\n## Author\n\nSuffolk Legal Innovation and Technology Lab, litlab@suffolk.edu\n',
      long_description_content_type='text/markdown',
      author='Suffolk Legal Innovation and Technology Lab',
      author_email='litlab@suffolk.edu',
      license='MIT',
      url='https://github.com/SuffolkLITLab/docassemble-LITEfile',
      packages=find_namespace_packages(include=["docassemble.LITEfile*"]),
      install_requires=['docassemble.AssemblyLine @ git+https://github.com/SuffolkLITLab/docassemble-AssemblyLine.git@main', 'requests>=2.31'],
      zip_safe=False,
      package_data=find_package_data(where='docassemble/LITEfile/', package='docassemble.LITEfile'),
     )
