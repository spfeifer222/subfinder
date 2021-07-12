# -*- coding: utf8 -*-
from __future__ import unicode_literals
import os
from subfinder.subsearcher.subsearcher import get_all_subsearchers
import re
import sys
import glob
import fnmatch
import logging
import mimetypes
import traceback
import requests
from .subsearcher.subsearcher import get_subsearcher, exceptions


class Pool(object):
    """ Simulating a thread pool or actually synchronizing execution code
    """

    def __init__(self, size):
        self.size = size

    def spawn(self, fn, *args, **kwargs):
        fn(*args, **kwargs)

    def join(self):
        return


class SubFinder(object):
    """ Subtitle Search Tool.
    """

    DEFAULT_VIDEO_EXTS = {'.mkv', '.mp4', '.ts', '.avi', '.wmv'}

    def __init__(self, path='./', languages=None, exts=None, subsearcher_class=None, **kwargs):
        self.set_path(path)
        self.languages = languages
        self.exts = exts
        self.subsearcher = []

        # silence: dont print anything
        self.silence = kwargs.get('silence', False)
        # logger's output
        self.logger_output = kwargs.get('logger_output', sys.stdout)
        # debug
        self.debug = kwargs.get('debug', False)
        # video_exts
        self.video_exts = set(self.__class__.DEFAULT_VIDEO_EXTS)
        if 'video_exts' in kwargs:
            video_exts = set(kwargs.get('video_exts'))
            self.video_exts.update(video_exts)
        # keyword
        self.keyword = kwargs.get('keyword')
        # ignore
        self.ignore = kwargs.get('ignore', False)
        # exclude
        self.exclude = kwargs.get('exclude', [])
        # api urls
        self.api_urls = kwargs.get('api_urls', {})
        # no-order-marker
        self.no_order_marker = kwargs.get('no_order_marker', False)

        self.kwargs = kwargs

        self._init_session()
        self._init_pool()
        self._init_logger()

        # _history: downloading history
        self._history = {}

        if subsearcher_class is None:
            subsearcher_class = list(get_all_subsearchers().values())
        if not isinstance(subsearcher_class, list):
            subsearcher_class = [subsearcher_class]
        self.subsearcher = subsearcher_class

    def _is_videofile(self, f):
        """ determine whether `f` is a valid video file, mostly base on file extension
        """
        if os.path.isfile(f):
            types = mimetypes.guess_type(f)
            mtype = types[0]
            if (mtype and mtype.split('/')[0] == 'video') or (os.path.splitext(f)[1] in self.video_exts):
                return True
        return False

    def _has_subtitles(self, f):
        """ Determine If f Already Has Local Captioning
        """
        dirname = os.path.dirname(f)
        basename = os.path.basename(f)
        basename_no_ext, _ = os.path.splitext(basename)
        exts = self.exts or ['ass', 'srt']
        for filename in os.listdir(dirname):
            _, ext = os.path.splitext(filename)
            ext = ext[1:]
            if filename.startswith(basename_no_ext) and ext in exts:
                return True
        return False

    def _fnmatch(self, f):
        for pattern in self.exclude:
            if fnmatch.fnmatchcase(f, pattern):
                return True
        return False

    def _filter_path(self, path):
        """ Filter all video files in path.
        """
        if self._is_videofile(path):
            if self._fnmatch(os.path.basename(path)):
                return
            if not self.ignore and self._has_subtitles(path):
                return
            yield path
            return

        if not os.path.isdir(path):
            return

        for root, dirs, files in os.walk(path):
            for filename in files:
                filepath = os.path.join(root, filename)
                if not self._is_videofile(filepath):
                    continue
                if self._fnmatch(filename):
                    continue
                if not self.ignore and self._has_subtitles(filepath):
                    continue
                yield filepath

            # remove dir in self.exclude
            removed_index = []
            for i, dirname in enumerate(dirs):
                if self._fnmatch(dirname + '/'):
                    removed_index.append(i)
            for i in removed_index:
                dirs.pop(i)

    def _init_session(self):
        """ initialization of requests.Session
        """
        self.session = requests.Session()
        self.session.mount('http://', adapter=requests.adapters.HTTPAdapter(
            pool_connections=10,
            pool_maxsize=100))

    def _init_pool(self):
        self.pool = Pool(10)

    def _init_logger(self):
        log_level = logging.INFO
        if self.silence:
            log_level = logging.CRITICAL + 1
        if self.debug:
            log_level = logging.DEBUG
        self.logger = logging.getLogger('SubFinder')
        self.logger.handlers = []
        self.logger.setLevel(log_level)
        sh = logging.StreamHandler(stream=self.logger_output)
        sh.setLevel(log_level)
        formatter = logging.Formatter(
            '[%(asctime)s]-[%(levelname)s]: %(message)s', datefmt='%m/%d %H:%M:%S')
        sh.setFormatter(formatter)
        self.logger.addHandler(sh)

    def _download(self, videofile):
        """ Call SubSearcher searching for and downloading subtitles
        """
        basename = os.path.basename(videofile)

        subinfos = []
        for subsearcher_cls in self.subsearcher:
            subsearcher = subsearcher_cls(self, api_urls=self.api_urls)
            self.logger.info('{0}：Start using {1} to search subtitles'.format(basename, subsearcher))
            try:
                subinfos = subsearcher.search_subs(videofile, self.languages, self.exts, self.keyword)
            except Exception as e:
                err = str(e)
                if self.debug:
                    err = traceback.format_exc()
                self.logger.error( '{}：Error while searching subtitles： {}'.format(basename, err))
                continue
            if subinfos:
                break
        self.logger.info('{1}：Found {0} subtitles, downltrnsoad'.format( len(subinfos), basename))
        for subinfo in subinfos:
            if isinstance(subinfo['subname'], (list, tuple)):
                self._history[videofile].extend(subinfo['subname'])
            else:
                self._history[videofile].append(subinfo['subname'])

    def set_path(self, path):
        path = os.path.abspath(path)
        self.path = path

    def start(self):
        """ SubFinder Start function
        """
        self.logger.info('Start')
        videofiles = list(self._filter_path(self.path))
        l = len(videofiles)
        if l > 1 and self.keyword:
            self.logger.warn('`keyword` should used only when there is one video file, but there is {} video files'.format(l))
            return
        for f in videofiles:
            self._history[f] = []
            self.pool.spawn(self._download, f)
        self.pool.join()
        self.logger.info('='*20 + 'Download complete' + '='*20)
        for v, subs in self._history.items():
            basename = os.path.basename(v)
            self.logger.info(
                '{}: {} subtitles downloaded'.format(basename, len(subs)))

    def done(self):
        pass
