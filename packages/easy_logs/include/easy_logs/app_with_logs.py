import copy
import os
from abc import ABCMeta

import duckietown_utils as dtu
from duckietown_utils.cli import D8App
from .logs_db import get_all_resources, invalidate_log_cache_because_downloaded, get_easy_logs_db2, \
    write_candidate_cloud
from .logs_structure import PhysicalLog
from .resource_desc import DTR, _create_file_uri

__all__ = ['D8AppWithLogs', 'download_if_necessary', 'get_log_if_not_exists']


class D8AppWithLogs(D8App):
    """
        An app that works with a log database.

    """

    def define_program_options(self, params):
        if hasattr(self, '_define_options_compmake'):
            self._define_options_compmake(params)
        self._define_my_options(params)
        self.define_options(params)

    def _define_my_options(self, params):
        g = "Options regarding the logs database"

        params.add_flag('cache_reset', help="Delete the local log caches if they exists.", group=g)

        params.add_flag('no_cloud', help="Do not use Cloud DB", group=g)
        params.add_flag('no_local', help="Do not use local DB", group=g)

        # advanced
        params.add_flag('write_candidate_cloud', help="Prepare cloud DB", group=g)
        self._db = None

    def get_easy_logs_db(self):
        if self._db is not None:
            return self._db

        do_not_use_cloud = self.options.no_cloud
        do_not_use_local = self.options.no_local

        do_write_candidate_cloud = self.options.write_candidate_cloud

        ignore_cache = self.options.cache_reset

        db = get_easy_logs_db2(do_not_use_cloud=do_not_use_cloud,
                               do_not_use_local=do_not_use_local, ignore_cache=ignore_cache)

        self._db = db

        if do_write_candidate_cloud:
            write_candidate_cloud(db.logs)

        return db


@dtu.contract(log=PhysicalLog, returns=PhysicalLog)
def download_if_necessary(log):
    """
        Downloads the log if necessary.

        Use like this:

            log = ...

            log2 = download_if_necessary(log)


    """
    # dtu.logger.info('Log:\n%s' % log.resources)
    dtu.check_isinstance(log, PhysicalLog)

    resource_name = 'bag'
    filename = get_log_if_not_exists(log, resource_name=resource_name)

    local_uri = _create_file_uri(filename)
    log.resources[resource_name]['urls'].append(local_uri)

    return log


@dtu.contract(log=PhysicalLog, returns=str)
def get_log_if_not_exists(log, resource_name):
    """" Returns the path to the log. """
    log = copy.deepcopy(log)
    dtu.logger.info('Get log if not exists: %s' % log.log_name)
    downloads = dtu.get_duckietown_local_log_downloads()

    dtr_yaml = log.resources[resource_name]
    dtr = DTR.from_yaml(dtr_yaml)

    all_resources = get_all_resources()
    if dtr.name in all_resources.basename2filename:
        # local!
        filename = all_resources.basename2filename[dtr.name]
        dtu.logger.info('We already have %s locally at %s' % (dtr.name, filename))
        return filename

    dtu.logger.info('We do not have %s locally.' % dtr.name)

    filename = os.path.join(downloads, dtr.name)
    if os.path.exists(filename):
        dtu.logger.info('It was already downloaded as %s' % filename)
        return filename

    use = []
    for url in dtr.urls:
        if url.startswith('http'):
            use.append(url)

    def priority(x):
        if '8080' in x:
            return 0
        else:
            return 1

    use.sort(key=priority)

    for url in use:
        try:
            dtu.d8n_make_sure_dir_exists(filename)
            dtu.download_if_not_exist(url, filename)
        except Exception as e:  # XXX
            dtu.logger.error(e)
        else:
            break
    else:
        msg = 'Could not download any file'
        raise Exception(msg)

    # invalidate cache
    invalidate_log_cache_because_downloaded()
    return filename
