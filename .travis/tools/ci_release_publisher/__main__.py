# -*- coding: utf-8 -*-

import argparse
import github
import logging
import os
import sys

from . import config
from . import env
from . import exception
from . import latest_release, numbered_release, tag_release
from . import temporary_draft_release
from . import travis
from .__version__ import __description__
from .cleanup_store_scope import CleanupStoreScope
from .cleanup_store_release import CleanupStoreRelease

logging.basicConfig(format='%(asctime)s %(message)s', level=logging.INFO, datefmt='%H:%M:%S')

release_kinds = [latest_release, numbered_release, tag_release]

parser = argparse.ArgumentParser(description=__description__)

# Travis
parser_travis = parser.add_mutually_exclusive_group()
parser_travis.add_argument('--travis-public', dest='travis_type', action='store_const', const='public',
                            help='Use API of the free Travis-CI service, i.e. "https://travis-ci.org".')
parser_travis.add_argument('--travis-private', dest='travis_type', action='store_const', const='private',
                            help='Use API of the paid Travis-CI service for GitHub private repositories, i.e. "https://travis-ci.com".')
parser_travis.add_argument('--travis-enterprise', dest='travis_type', metavar='TRAVIS_URL', type=str,
                            help='Use API of Travis-CI running under a personal domain. Specify the Travis-CI instance URL, not the API endpoint URL, e.g. "https://travis.example.com".')
parser.set_defaults(travis_type='public')

parser.add_argument('--github-api-url', type=str, default="",
                    help='Use custom GitHib API URL, e.g. for self-hosted GitHub Enterprise instance. This should be an URL to the API endpoint, e.g. "https://api.github.com".')

parser.add_argument('--tag-prefix', type=str, default=config.tag_prefix, help='git tag prefix to use.')
parser.add_argument('--tag-prefix-tmp', type=str, default=config.tag_prefix_tmp, help='git tag prefix to use for temporary in-progress releases.')

subparsers = parser.add_subparsers(dest='command')

# store subparser
parser_store = subparsers.add_parser('store', help='Store artifacts of this job in a draft release for the later collection in the "publish" command job.')
parser_store.add_argument('artifact_dir', metavar='artifact-dir', help='Path to a directory containing artifacts that need to be stored.')
temporary_draft_release.args(parser_store)

# cleanup store subparser

def _enum_to_choices(enum_calss):
    return [e.name.lower().replace('_', '-') for e in enum_calss]

def _choices_to_enum(enum_calss, choices):
    return [enum_calss[s.upper().replace('-', '_')] for s in choices]

parser_cleanup_store = subparsers.add_parser('cleanup_store', help='Delete the releases created by the "store" command.')
parser_cleanup_store.add_argument('--scope', nargs='+', type=str, choices=_enum_to_choices(CleanupStoreScope), required=True, help="Scope to cleanup.")
parser_cleanup_store.add_argument('--release', nargs='+', type=str, choices=_enum_to_choices(CleanupStoreRelease), required=True, help="Release to cleanup.")
parser_cleanup_store.add_argument('--on-nonallowed-failure', default=False, action='store_true', help='Cleanup only if the current build has a job that both has failed and doesn\'t have allow_failure set on it, i.e. the current build is going to fail once the current stage finishes running.')

# collect subparser
parser_collect = subparsers.add_parser('collect', help='Collect artifacts from all draft releases created by the "store" command during this build in a directory.')
parser_collect.add_argument('artifact_dir', metavar='artifact-dir', help='Path to a directory where artifacts should be collected to.')

# publish subparser
parser_publish = subparsers.add_parser('publish', help='Publish a release with all artifacts from a directory.')
parser_publish.add_argument('artifact_dir', metavar='artifact-dir', help='Path to a directory containing build artifacts to publish.')

# cleanup publish subparser
parser_cleanup_publish = subparsers.add_parser('cleanup_publish', help='Delete incomplete releases left over by the "publish" command.')

for r in release_kinds:
    r.publish_args(parser_publish)

args = parser.parse_args()

# Sanity-check arguments

travis_url = args.travis_type
travis_api_url = '{}/api'.format(travis_url)
if args.travis_type == 'public':
    travis_url = 'https://travis-ci.org'
    travis_api_url = 'https://api.travis-ci.org'
elif args.travis_type == 'private':
    travis_url = 'https://travis-ci.com'
    travis_api_url = 'https://api.travis-ci.com'

if not args.github_api_url:
    args.github_api_url = "https://api.github.com"

try:
    if not args.tag_prefix:
        raise exception.CIReleasePublisherError('--tag-prefix can\'t be empty.')
    if not args.tag_prefix_tmp:
        raise exception.CIReleasePublisherError('--tag-prefix-tmp can\'t be empty.')
    config.tag_prefix = args.tag_prefix
    config.tag_prefix_tmp = args.tag_prefix_tmp

    if args.command == 'store':
        if not os.path.isdir(args.artifact_dir):
            raise exception.CIReleasePublisherError('Directory "{}" doesn\'t exist.'.format(args.artifact_dir))
        if len(os.listdir(args.artifact_dir)) <= 0:
            raise exception.CIReleasePublisherError('No artifacts were found in "{}" directory.'.format(args.artifact_dir))
        temporary_draft_release.publish_validate_args(args)
        releases = github.Github(login_or_token=env.required('GITHUB_ACCESS_TOKEN'), base_url=args.github_api_url).get_repo(env.required('TRAVIS_REPO_SLUG')).get_releases()
        temporary_draft_release.publish_with_args(args, releases, args.artifact_dir, args.github_api_url, travis_api_url, travis_url)
    elif args.command == 'cleanup_store':
        releases = github.Github(login_or_token=env.required('GITHUB_ACCESS_TOKEN'), base_url=args.github_api_url).get_repo(env.required('TRAVIS_REPO_SLUG')).get_releases()
        temporary_draft_release.cleanup_store(releases, _choices_to_enum(CleanupStoreScope, args.scope), _choices_to_enum(CleanupStoreRelease, args.release), args.on_nonallowed_failure, args.github_api_url)
    elif args.command == 'collect':
        if not os.path.isdir(args.artifact_dir):
            raise exception.CIReleasePublisherError('Directory "{}" doesn\'t exist.'.format(args.artifact_dir))
        releases = github.Github(login_or_token=env.required('GITHUB_ACCESS_TOKEN'), base_url=args.github_api_url).get_repo(env.required('TRAVIS_REPO_SLUG')).get_releases()
        temporary_draft_release.download(releases, args.artifact_dir)
    elif args.command == 'publish':
        if not os.path.isdir(args.artifact_dir):
            raise exception.CIReleasePublisherError('Directory "{}" doesn\'t exist.'.format(args.artifact_dir))
        if len(os.listdir(args.artifact_dir)) <= 0:
            raise exception.CIReleasePublisherError('No artifacts were found in "{}" directory.'.format(args.artifact_dir))
        if not any(r.publish_validate_args(args) for r in release_kinds):
            raise exception.CIReleasePublisherError('You must specify what kind of release you would like to publish.')
        releases = github.Github(login_or_token=env.required('GITHUB_ACCESS_TOKEN'), base_url=args.github_api_url).get_repo(env.required('TRAVIS_REPO_SLUG')).get_releases()
        for r in release_kinds:
            r.publish_with_args(args, releases, args.artifact_dir, args.github_api_url, travis_api_url, travis_url)
    elif args.command == 'cleanup_publish':
        releases = github.Github(login_or_token=env.required('GITHUB_ACCESS_TOKEN'), base_url=args.github_api_url).get_repo(env.required('TRAVIS_REPO_SLUG')).get_releases()
        branch_unfinished_build_numbers = travis.Travis.github_auth(env.required('GITHUB_ACCESS_TOKEN'), travis_api_url).branch_unfinished_build_numbers(env.required('TRAVIS_REPO_SLUG'), env.required('TRAVIS_BRANCH'))
        for r in release_kinds:
            r.cleanup(releases, branch_unfinished_build_numbers, args.github_api_url)
    else:
        raise exception.CIReleasePublisherError('Specify one of "store", "cleanup_store", "collect", "cleanup" or "publish" commands.')
except exception.CIReleasePublisherError as e:
    logging.error('Error: {}'.format(str(e)))
    sys.exit(1)
