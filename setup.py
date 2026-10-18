#!/usr/bin/env python
"""Packaging metadata.

This fork keeps the distribution name ``jmespath`` so it is a drop-in
replacement, but adds the project URLs and a post-release version suffix so an
installed fork can be told apart from the PyPI release.
"""
import io

from setuptools import find_packages, setup

VERSION = '1.1.0'

setup(
    name='jmespath',
    version=VERSION + '.post1',
    description='JSON Matching Expressions (compiled-plan fork, 2.7x-7.7x '
                'faster searches)',
    long_description=io.open('README.rst', encoding='utf-8').read(),
    long_description_content_type='text/x-rst',
    author='James Saryerwinnie',
    author_email='js@jamesls.com',
    maintainer='linluozi',
    url='https://github.com/linluozi/jmespath-compiled-plans',
    project_urls={
        'Source': 'https://github.com/linluozi/jmespath-compiled-plans',
        'Upstream': 'https://github.com/jmespath/jmespath.py',
        'Optimization notes':
            'https://github.com/linluozi/jmespath-compiled-plans/blob/main/'
            'OPTIMIZATION.md',
    },
    scripts=['bin/jp.py'],
    packages=find_packages(exclude=['tests', 'tests.*', 'bench']),
    license='MIT',
    python_requires='>=3.9',
    zip_safe=False,
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: Developers',
        'Natural Language :: English',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Programming Language :: Python :: 3.13',
        'Programming Language :: Python :: 3.14',
        'Programming Language :: Python :: Implementation :: CPython',
        'Programming Language :: Python :: Implementation :: PyPy',
        'Topic :: Software Development :: Libraries',
        'Topic :: Text Processing :: Markup',
    ],
)
