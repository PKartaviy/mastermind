#!/usr/bin/env python
import logging
import time

from cocaine.worker import Worker
import elliptics

import log
log.setup_logger('mm_cache_logging')
logger = logging.getLogger('mm.init')

from config import config
import cache
from db.mongo.pool import MongoReplicaSetClient
import infrastructure
import infrastructure_cache
import helpers as h


def init_elliptics_node():
    nodes = config.get('elliptics', {}).get('nodes', []) or config["elliptics_nodes"]
    logger.debug("config: %s" % str(nodes))

    log = elliptics.Logger(str(config["dnet_log"]), config["dnet_log_mask"])

    node_config = elliptics.Config()
    node_config.io_thread_num = config.get('io_thread_num', 1)
    node_config.nonblocking_io_thread_num = config.get('nonblocking_io_thread_num', 1)
    node_config.net_thread_num = config.get('net_thread_num', 1)

    logger.info('Node config: io_thread_num {0}, nonblocking_io_thread_num {1}, '
        'net_thread_num {2}'.format(node_config.io_thread_num, node_config.nonblocking_io_thread_num,
            node_config.net_thread_num))

    n = elliptics.Node(log, node_config)

    addresses = [elliptics.Address(host=str(node[0]), port=node[1], family=node[2])
                 for node in nodes]

    try:
        n.add_remotes(addresses)
    except Exception as e:
        logger.error('Failed to connect to any elliptics storage node: {0}'.format(
            e))
        raise ValueError('Failed to connect to any elliptics storage node')

    meta_node = elliptics.Node(log, node_config)

    addresses = [elliptics.Address(host=str(node[0]), port=node[1], family=node[2])
                 for node in config["metadata"]["nodes"]]
    logger.info('Connecting to meta nodes: {0}'.format(config["metadata"]["nodes"]))

    try:
        meta_node.add_remotes(addresses)
    except Exception as e:
        logger.error('Failed to connect to any elliptics meta storage node: {0}'.format(
            e))
        raise ValueError('Failed to connect to any elliptics storage META node')

    meta_wait_timeout = config['metadata'].get('wait_timeout', 5)

    meta_session = elliptics.Session(meta_node)
    meta_session.set_timeout(meta_wait_timeout)
    meta_session.add_groups(list(config["metadata"]["groups"]))
    n.meta_session = meta_session

    wait_timeout = config.get('elliptics', {}).get('wait_timeout', 5)
    time.sleep(wait_timeout)

    return n

def init_meta_db():
    meta_db = None

    mrsc_options = config['metadata'].get('options', {})

    if config['metadata'].get('url'):
        meta_db = MongoReplicaSetClient(config['metadata']['url'], **mrsc_options)
    return meta_db

def init_infrastructure_cache_manager(W, n):
    icm = infrastructure_cache.InfrastructureCacheManager(n.meta_session)
    return icm

def init_infrastructure(W, n):
    infstruct = infrastructure.infrastructure
    infstruct.init(n)
    return infstruct

def init_cache_worker(W, n, meta_db):
    c = cache.CacheManager(n, meta_db)
    h.register_handle(W, c.ping)
    h.register_handle(W, c.get_top_keys)
    h.register_handle(W, c.test_get_groups_list)
    h.register_handle(W, c.test_distribute)
    h.register_handle(W, c.cache_statistics)

    return c


if __name__ == '__main__':

    n = init_elliptics_node()

    logger.info("before creating worker")
    W = Worker(disown_timeout=config.get('disown_timeout', 2))
    logger.info("after creating worker")

    meta_db = init_meta_db()
    if meta_db is None:
        s = 'Meta db should be configured in "metadata" config section'
        logger.error(s)
        raise RuntimeError(s)

    i = init_infrastructure(W, n)
    icm = init_infrastructure_cache_manager(W, n)
    c = init_cache_worker(W, n, meta_db)

    icm._start_tq()
    c._start_tq()

    logger.info("Starting cache worker")
    W.run()
    logger.info("Cache worker initialized")