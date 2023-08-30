import pathlib
import re
import setuptools


def parse_requirements(filename):
  requirements = pathlib.Path(filename)
  requirements = requirements.read_text().split('\n')
  requirements = [x for x in requirements if x.strip()]
  return requirements


def parse_version(filename):
  text = (pathlib.Path(__file__).parent / filename).read_text()
  version = re.search(r"__version__ = '(.*)'", text).group(1)
  return version


setuptools.setup(
    name='zerofun',
    version=parse_version('zerofun/__init__.py'),
    author='Danijar Hafner',
    author_email='mail@danijar.com',
    description='Remote function calls for array data using ZMQ',
    url='http://github.com/danijar/zerofun',
    long_description=pathlib.Path('README.md').read_text(),
    long_description_content_type='text/markdown',
    packages=['zerofun'],
    include_package_data=True,
    install_requires=parse_requirements('requirements.txt'),
    classifiers=[
        'Intended Audience :: Science/Research',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3',
    ],
)
