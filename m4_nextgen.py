#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Oct 22 11:09:36 2019

@author: mulderg
"""

from logging import basicConfig, getLogger
#from logging import DEBUG as log_level
from logging import INFO as log_level
basicConfig(level = log_level,
            format  = '%(asctime)s %(levelname)-8s %(module)-20s: %(message)s',
            datefmt ='%Y-%m-%d %H:%M:%S')
logger = getLogger(__name__)

import numpy as np
from pprint import pformat
from datetime import date

from hyperopt import fmin, rand, hp, space_eval, STATUS_FAIL, STATUS_OK
from hyperopt.mongoexp import MongoTrials
from os import environ

########################################################################################################
        
#rand_seed = 42

if "VERSION" in environ:    
    version = environ.get("VERSION")
    logger.info("Using version : %s" % version)
    
    use_cluster = True
else:
    version = "final"
    logger.warning("VERSION not set, using: %s" % version)
    
    use_cluster = False

if "DATASET" in environ:    
    dataset_name = environ.get("DATASET")
    logger.info("Using dataset : %s" % dataset_name)
    
    use_cluster = True
else:
    dataset_name = "m3_monthly"
    logger.warning("DATASET not set, using: %s" % dataset_name)
    
freq_pd = "M"
freq = 12
prediction_length = 1

def smape(a, b):
    """
    Calculates sMAPE
    :param a: actual values
    :param b: predicted values
    :return: sMAPE
    """
    a = np.reshape(a, (-1,))
    b = np.reshape(b, (-1,))
    return np.mean(2.0 * np.abs(a - b) / (np.abs(a) + np.abs(b))).item()

def mase(insample, y_test, y_hat_test, freq):
    """
    Calculates MASE
    :param insample: insample data
    :param y_test: out of sample target values
    :param y_hat_test: predicted values
    :param freq: data frequency
    :return:
    """
    y_hat_naive = []
    for i in range(freq, len(insample)):
        y_hat_naive.append(insample[(i - freq)])

    masep = np.mean(abs(insample[freq:] - y_hat_naive))

    return np.mean(abs(y_test - y_hat_test)) / masep

def compute_horiz_errs(test_data, forecasts, num_ts):
    y_hats = {}
#    smapes, smapes06, smapes12, smapes18 = [], [], [], []
#    mases, mases06, mases12, mases18 = [], [], [], []
    
    for idx in range(num_ts):
#        in_sample = test_data[idx]['target'][:-prediction_length]
#
#        y_test = test_data[idx]['target'][-prediction_length:]
#        y_test06 = y_test[-prediction_length:(-prediction_length+6)]
#        y_test12 = y_test[(-prediction_length+6):(-prediction_length+12)]
#        y_test18 = y_test[-6:]

        y_hat = forecasts[idx].samples.reshape(-1)
        y_hats[str(idx)] = y_hat.tolist()
#        y_hat06 = y_hat[-prediction_length:(-prediction_length+6)]
#        y_hat12 = y_hat[(-prediction_length+6):(-prediction_length+12)]
#        y_hat18 = y_hat[-6:]
#
#        smapes.append(smape(y_test, y_hat))
#        mases.append(mase(in_sample, y_test, y_hat, freq))
#
#        smapes06.append(smape(y_test06, y_hat06))
#        mases06.append(mase(in_sample, y_test06, y_hat06, freq))
#
#        smapes12.append(smape(y_test12, y_hat12))        
#        mases12.append(mase(in_sample, y_test12, y_hat12, freq))
#
#        smapes18.append(smape(y_test18, y_hat18))
#        mases18.append(mase(in_sample, y_test18, y_hat18, freq))
#    
#    errs = {
#        'smape'   : np.nanmean(smapes),
#        'mase'    : np.nanmean(mases),
#
#        'smape06' : np.nanmean(smapes06),
#        'mase06'  : np.nanmean(mases06),
#    
#        'smape12' : np.nanmean(smapes12),
#        'mase12'  : np.nanmean(mases12),
#
#        'smape18' : np.nanmean(smapes18),
#        'mase18'  : np.nanmean(mases18),
#    }
    
    print(y_hats)
    return y_hats

def score_model(model, model_type, gluon_test_data, num_ts):
    import mxnet as mx
    from gluonts.evaluation.backtest import make_evaluation_predictions #, backtest_metrics
    from gluonts.evaluation import Evaluator
    from gluonts.model.predictor import Predictor
    from tempfile import mkdtemp
    from pathlib import Path
    from itertools import tee
 
    if model_type != "DeepStateEstimator":
        forecast_it, ts_it = make_evaluation_predictions(dataset=gluon_test_data, predictor=model, num_samples=1)
    else:
        temp_dir_path = mkdtemp()
        model.serialize(Path(temp_dir_path))
        model_cpu = Predictor.deserialize(Path(temp_dir_path), ctx=mx.cpu())
        logger.info("Loaded DeepState model")
        forecast_it, ts_it = make_evaluation_predictions(dataset=gluon_test_data, predictor=model_cpu, num_samples=1)
        logger.info("Evaluated DeepState model")

    forecast_it1, forecast_it2 = tee(forecast_it)
    agg_metrics, _ = Evaluator()(ts_it, forecast_it1, num_series=num_ts)

    forecasts = list(forecast_it2)
        
    return agg_metrics, forecasts

def get_trainer_hyperparams(model_cfg):
    # Trainer hyperparams have a "+" in them so we can pick them off
    trainer_cfg = {}
    for key in model_cfg.keys():
         if '+' in key:
            key_split = key.split('+', 1)[1]
            trainer_cfg[key_split] = model_cfg[key]
    return trainer_cfg

def load_data(path, model_type):
    from json import loads
    
    data = {}
    for dataset in ["train", "test"]:
        data[dataset] = []
        fname = "%s/%s/data.json" % (path, dataset)
        logger.info("Reading data from: %s" % fname)
        with open(fname) as fp:
            for line in fp:
               ts_data = loads(line)
               
               # Remove static features if not supported by model
               if model_type in ['SimpleFeedForwardEstimator',
                                 'DeepFactorEstimator',
                                 'GaussianProcessEstimator']:
                   del(ts_data['feat_static_cat'])
                   
               data[dataset].append(ts_data)
               
        logger.info("Loaded %d time series from %s/%s" % (len(data[dataset]), dataset_name, dataset))

    return data
    
def forecast(cfg):    
    import mxnet as mx
    from gluonts.dataset.common import ListDataset
    from gluonts.model.simple_feedforward import SimpleFeedForwardEstimator
    from gluonts.model.deep_factor import DeepFactorEstimator
    from gluonts.model.gp_forecaster import GaussianProcessEstimator
#    from gluonts.kernels import RBFKernelOutput, KernelOutputDict
    from gluonts.model.wavenet import WaveNetEstimator
    from gluonts.model.transformer import TransformerEstimator
    from gluonts.model.deepar import DeepAREstimator
    from gluonts.model.deepstate import DeepStateEstimator
    from gluonts.trainer import Trainer
    from gluonts import distribution
    
    logger.info("Params: %s " % cfg)
    mx.random.seed(cfg['rand_seed'], ctx='all')
    np.random.seed(cfg['rand_seed'])

    # Load training data
    train_data  = load_data("/var/tmp/%s_all" % dataset_name, cfg['model']['type'])
    num_ts = len(train_data['train'])
    
#    trainer=Trainer(
#        epochs=3,
#        hybridize=False,
#    )

    trainer_cfg = get_trainer_hyperparams(cfg['model'])
    trainer=Trainer(
        mx.Context("gpu"),
        hybridize=False,
        epochs=trainer_cfg['max_epochs'],
        num_batches_per_epoch=trainer_cfg['num_batches_per_epoch'],
        batch_size=trainer_cfg['batch_size'],
        patience=trainer_cfg['patience'],
        
        learning_rate=trainer_cfg['learning_rate'],
        learning_rate_decay_factor=trainer_cfg['learning_rate_decay_factor'],
        minimum_learning_rate=trainer_cfg['minimum_learning_rate'],
        clip_gradient=trainer_cfg['clip_gradient'],
        weight_decay=trainer_cfg['weight_decay'],
    )

    if cfg['box_cox']:
        distr_output=distribution.TransformedDistributionOutput(distribution.GaussianOutput(),
                                                                    [distribution.InverseBoxCoxTransformOutput(lb_obs=-1.0E-5)])
    else:
        distr_output=distribution.StudentTOutput()
        
    if cfg['model']['type'] == 'SimpleFeedForwardEstimator':
        estimator = SimpleFeedForwardEstimator(
            freq=freq_pd,
            prediction_length=prediction_length,
            num_hidden_dimensions = cfg['model']['num_hidden_dimensions'],
            num_parallel_samples=1,
            trainer=trainer,
            distr_output=distr_output)
        
    if cfg['model']['type'] == 'DeepFactorEstimator': 
         estimator = DeepFactorEstimator(
            freq=freq_pd,
            prediction_length=prediction_length,
            num_hidden_global=cfg['model']['num_hidden_global'], 
            num_layers_global=cfg['model']['num_layers_global'], 
            num_factors=cfg['model']['num_factors'], 
            num_hidden_local=cfg['model']['num_hidden_local'], 
            num_layers_local=cfg['model']['num_layers_local'],
            trainer=trainer,
            distr_output=distr_output)

    if cfg['model']['type'] == 'GaussianProcessEstimator':
#        if cfg['model']['rbf_kernel_output']:
#            kernel_output = RBFKernelOutput()
#        else:
#            kernel_output = KernelOutputDict()
        estimator = GaussianProcessEstimator(
            freq=freq_pd,
            prediction_length=prediction_length,
            cardinality=num_ts,
            max_iter_jitter=cfg['model']['max_iter_jitter'],
            sample_noise=cfg['model']['sample_noise'],
            num_parallel_samples=1,
            trainer=trainer)

    if cfg['model']['type'] == 'WaveNetEstimator':            
        estimator = WaveNetEstimator(
            freq=freq_pd,
            prediction_length=prediction_length,
            cardinality=[num_ts, 6],
            embedding_dimension=cfg['model']['embedding_dimension'],
            num_bins=cfg['model']['num_bins'],        
            n_residue=cfg['model']['n_residue'],
            n_skip=cfg['model']['n_skip'],
            dilation_depth=cfg['model']['dilation_depth'], 
            n_stacks=cfg['model']['n_stacks'],
            act_type=cfg['model']['wn_act_type'],
            num_parallel_samples=1,
            trainer=trainer)
                 
    if cfg['model']['type'] == 'TransformerEstimator':
        if cfg['model']['tf_use_xreg']:
            cardinality=[num_ts, 6]
        else:
            cardinality=None
            
        estimator = TransformerEstimator(
            freq=freq_pd,
            prediction_length=prediction_length,
            use_feat_static_cat=cfg['model']['tf_use_xreg'],
            cardinality=cardinality,
            model_dim=cfg['model']['model_dim_heads'][0], 
            inner_ff_dim_scale=cfg['model']['inner_ff_dim_scale'],
            pre_seq=cfg['model']['pre_seq'], 
            post_seq=cfg['model']['post_seq'], 
            act_type=cfg['model']['tf_act_type'], 
            num_heads=cfg['model']['model_dim_heads'][1], 
            dropout_rate=cfg['model']['tf_dropout_rate'],
            num_parallel_samples=1,
            trainer=trainer,
            distr_output=distr_output)

    if cfg['model']['type'] == 'DeepAREstimator':
        if cfg['model']['da_use_xreg']:
            cardinality=[num_ts, 6]
        else:
            cardinality=None
            
        estimator = DeepAREstimator(
            freq=freq_pd,
            prediction_length=prediction_length,        
            use_feat_static_cat=cfg['model']['da_use_xreg'],
            cardinality=cardinality,
            cell_type=cfg['model']['da_cell_type'],
            num_cells=cfg['model']['da_num_cells'],
            num_layers=cfg['model']['da_num_layers'],        
            dropout_rate=cfg['model']['da_dropout_rate'],
            num_parallel_samples=1,
            trainer=trainer,
            distr_output=distr_output)

    if cfg['model']['type'] == 'DeepStateEstimator':            
        estimator = DeepStateEstimator(
            freq=freq_pd,
            prediction_length=prediction_length,
#            cell_type=cfg['model']['ds_cell_type'],
#            add_trend=cfg['model']['add_trend'],     
#            num_cells=cfg['model']['ds_num_cells'],
#            num_layers=cfg['model']['ds_num_layers'],    
#            num_periods_to_train=cfg['model']['num_periods_to_train'],    
#            dropout_rate=cfg['model']['ds_dropout_rate'],
            use_feat_static_cat=True,
            cardinality=[num_ts, 6],
            num_parallel_samples=1,
            trainer=trainer)
    
    logger.info("Fitting: %s" % estimator)
    gluon_train = ListDataset(train_data['train'].copy(), freq=freq_pd)
    model = estimator.train(gluon_train)
    gluon_validate = ListDataset(train_data['test'].copy(), freq=freq_pd)
    validate_errs, forecasts = score_model(model, cfg['model']['type'], gluon_validate, num_ts)
    logger.info("Validation error: %s" % validate_errs)

#    test_data = load_data("/var/tmp/%s_all" % dataset_name, cfg['model']['type'])
#    gluon_test = ListDataset(test_data['test'].copy(), freq=freq_pd)
#    test_errs, forecasts = score_model(model, cfg['model']['type'], gluon_test, num_ts)
#    logger.info("Testing error : %s" % test_errs)

    y_hats = compute_horiz_errs(train_data['test'], forecasts, num_ts)
#    logger.info("Horizon error : %s" % horiz_errs)
    
    return {
        'validate' : validate_errs,
#        'test'     : test_errs,
#        'horizon'  : horiz_errs,
        'y_hats'   : y_hats
    }

def gluonts_fcast(cfg):   
    from traceback import format_exc
    from os import environ as local_environ
    
    try:
        err_metrics = forecast(cfg)
        if np.isnan(err_metrics['validate']['MASE']):
            raise ValueError("Validation MASE is NaN")
        if np.isinf(err_metrics['validate']['MASE']):
           raise ValueError("Validation MASE is infinite")
           
    except Exception as e:                    
        exc_str = format_exc()
        logger.error('\n%s' % exc_str)
        return {
            'loss'        : None,
            'status'      : STATUS_FAIL,
            'cfg'         : cfg,
            'exception'   : exc_str,
            'build_url'   : local_environ.get("BUILD_URL")
        }
        
    return {
        'loss'        : err_metrics['validate']['MASE'],
        'status'      : STATUS_OK,
        'cfg'         : cfg,
        'err_metrics' : err_metrics,
        'build_url'   : local_environ.get("BUILD_URL")
    }

def call_hyperopt():

#    # Trainer hyperparams common to all models
#    max_epochs = [32, 64, 128, 256, 512, 1024]
#    num_batches_per_epoch = [32, 64, 128, 256, 512]
#    batch_size = [32, 64, 128, 256]
#    patience = [8, 16, 32, 64]
#    learning_rate = {
#        'min' : np.log(05e-04),
#        'max' : np.log(50e-04)
#    }
#    learning_rate_decay_factor = {
#        'min' : 0.10,
#        'max' : 0.75
#    }
#    minimum_learning_rate = {
#        'min' : np.log(005e-06),
#        'max' : np.log(100e-06)
#    }
#    weight_decay = {
#        'min' : np.log(01e-09),
#        'max' : np.log(100e-09)
#    }
#    clip_gradient = {
#        'min' :  1,
#        'max' : 10
#    }
#    
#    dropout_rate = {
#        'min' : 0.07,
#        'max' : 0.13
#    }
    
    space = {
        'box_cox' : hp.choice('box_cox', [False]),
        'rand_seed' : hp.choice('rand_seed', list(range(10000))),
        'model' : hp.choice('model', [
            {
                'type'                           : 'SimpleFeedForwardEstimator',
                'num_hidden_dimensions'          : hp.choice('num_hidden_dimensions', [[16, 8]]),
                   
                'sff+max_epochs'                 : hp.choice('sff+max_epochs', [1024]),
                'sff+num_batches_per_epoch'      : hp.choice('sff+num_batches_per_epoch', [32]),
                'sff+batch_size'                 : hp.choice('sff+batch_size', [256]),
                'sff+patience'                   : hp.choice('sff+patience', [64]),
                
                'sff+learning_rate'              : hp.choice('sff+learning_rate', [0.0008106323839487305]),
                'sff+learning_rate_decay_factor' : hp.choice('sff+learning_rate_decay_factor', [0.7088613640841722]),
                'sff+minimum_learning_rate'      : hp.choice('sff+minimum_learning_rate', [2.3403617408667674e-7]),
                'sff+weight_decay'               : hp.choice('sff+weight_decay', [1.068262923373402e-8]),
                'sff+clip_gradient'              : hp.choice('sff+clip_gradient', [10]), 
            },

#            {
#                'type'                           : 'DeepFactorEstimator',
#                'num_hidden_global'              : hp.choice('num_hidden_global', [2, 4, 8, 16, 32, 64, 128, 256]),
#                'num_layers_global'              : hp.choice('num_layers_global', [1, 2, 3]),
#                'num_factors'                    : hp.choice('num_factors', [2, 4, 8, 16, 32]),
#                'num_hidden_local'               : hp.choice('num_hidden_local', [2, 4, 8]),
#                'num_layers_local'               : hp.choice('num_layers_local', [1, 2, 3]),
#
#                'df+max_epochs'                  : hp.choice('df+max_epochs', max_epochs),
#                'df+num_batches_per_epoch'       : hp.choice('df+num_batches_per_epoch', num_batches_per_epoch),
#                'df+batch_size'                  : hp.choice('df+batch_size', batch_size),
#                'df+patience'                    : hp.choice('df+patience', patience),
#                
#                'df+learning_rate'               : hp.loguniform('df+learning_rate', learning_rate['min'], learning_rate['max']),
#                'df+learning_rate_decay_factor'  : hp.uniform('df+learning_rate_decay_factor', learning_rate_decay_factor['min'], learning_rate_decay_factor['max']),
#                'df+minimum_learning_rate'       : hp.loguniform('df+minimum_learning_rate', minimum_learning_rate['min'], minimum_learning_rate['max']),
#                'df+weight_decay'                : hp.loguniform('df+weight_decay', weight_decay['min'], weight_decay['max']),
#                'df+clip_gradient'               : hp.uniform('df+clip_gradient', clip_gradient['min'], clip_gradient['max']), 
#            },
                    
#            {
#                'type'                           : 'GaussianProcessEstimator',
##                'rbf_kernel_output'              : hp.choice('rbf_kernel_output', [True, False]),
#                'max_iter_jitter'                : hp.choice('max_iter_jitter', [8]),
#                'sample_noise'                   : hp.choice('sample_noise', [False]),
#                
#                'gp+max_epochs'                  : hp.choice('gp+max_epochs', [2048]),
#                'gp+num_batches_per_epoch'       : hp.choice('gp+num_batches_per_epoch', [256]),
#                'gp+batch_size'                  : hp.choice('gp+batch_size', [128]),
#                'gp+patience'                    : hp.choice('gp+patience', [16]),
#                
#                'gp+learning_rate'               : hp.choice('gp+learning_rate', [0.001680069693528867]),
#                'gp+learning_rate_decay_factor'  : hp.choice('gp+learning_rate_decay_factor', [0.6153225279823602]),
#                'gp+minimum_learning_rate'       : hp.choice('gp+minimum_learning_rate', [8.154688047405645e-7]),
#                'gp+weight_decay'                : hp.choice('gp+weight_decay', [1.4523274501810558e-7]),
#                'gp+clip_gradient'               : hp.choice('gp+clip_gradient', [10]), 
#
#            },
                  
            {
                'type'                           : 'WaveNetEstimator',
                'embedding_dimension'            : hp.choice('embedding_dimension', [2]),
                'num_bins'                       : hp.choice('num_bins', [2048]),
                'n_residue'                      : hp.choice('n_residue', [24]),
                'n_skip'                         : hp.choice('n_skip', [64]),
                'dilation_depth'                 : hp.choice('dilation_depth', [4]),
                'n_stacks'                       : hp.choice('n_stacks', [1]),
                'wn_act_type'                    : hp.choice('wn_act_type', ['sigmoid']),
                
                'wn+max_epochs'                  : hp.choice('wn+max_epochs', [128]),
                'wn+num_batches_per_epoch'       : hp.choice('wn+num_batches_per_epoch', [512]),
                'wn+batch_size'                  : hp.choice('wn+batch_size', [64]),
                'wn+patience'                    : hp.choice('wn+patience', [16]),
                
                'wn+learning_rate'               : hp.choice('wn+learning_rate', [0.0010087136837619813]),
                'wn+learning_rate_decay_factor'  : hp.choice('wn+learning_rate_decay_factor', [0.46514468191968394]),
                'wn+minimum_learning_rate'       : hp.choice('wn+minimum_learning_rate', [5.217627637902781e-7]),
                'wn+weight_decay'                : hp.choice('wn+weight_decay', [1.1021116955627144e-7]),
                'wn+clip_gradient'               : hp.choice('wn+clip_gradient', [10]),
            },
                   
            {
                'type'                           : 'TransformerEstimator',
                'tf_use_xreg'                    : hp.choice('tf_use_xreg', [True]),
                'model_dim_heads'                : hp.choice('model_dim_heads', [[16, 4]]),
                'inner_ff_dim_scale'             : hp.choice('inner_ff_dim_scale', [3]),
                'pre_seq'                        : hp.choice('pre_seq', ['nd']),
                'post_seq'                       : hp.choice('post_seq', ['d']),
                'tf_act_type'                    : hp.choice('tf_act_type', ['relu']),               
                'tf_dropout_rate'                : hp.choice('tf_dropout_rate', [0.09227239771946842]),
                
                'tf+max_epochs'                  : hp.choice('tf+max_epochs', [1024]),
                'tf+num_batches_per_epoch'       : hp.choice('tf+num_batches_per_epoch', [512]),
                'tf+batch_size'                  : hp.choice('tf+batch_size', [32]),
                'tf+patience'                    : hp.choice('tf+patience', [32]),
                
                'tf+learning_rate'               : hp.choice('tf+learning_rate', [0.0006788500270020236]),
                'tf+learning_rate_decay_factor'  : hp.choice('tf+learning_rate_decay_factor', [0.3538768351296254]),
                'tf+minimum_learning_rate'       : hp.choice('tf+minimum_learning_rate', [6.596578997096586e-7]),
                'tf+weight_decay'                : hp.choice('tf+weight_decay', [3.570848234535124e-8]),
                'tf+clip_gradient'               : hp.choice('tf+clip_gradient', [10]),
            },

            {
                'type'                           : 'DeepAREstimator',
                'da_cell_type'                   : hp.choice('da_cell_type', ['lstm']),
                'da_use_xreg'                    : hp.choice('da_use_xreg', [True]),
                'da_num_cells'                   : hp.choice('da_num_cells', [256]),
                'da_num_layers'                  : hp.choice('da_num_layers', [1]),
                
                'da_dropout_rate'                : hp.choice('da_dropout_rate', [0.1089075818868427]),
                
                'da+max_epochs'                  : hp.choice('da+max_epochs', [128]),
                'da+num_batches_per_epoch'       : hp.choice('da+num_batches_per_epoch', [128]),
                'da+batch_size'                  : hp.choice('da+batch_size', [256]),
                'da+patience'                    : hp.choice('da+patience', [16]),
                
                'da+learning_rate'               : hp.choice('da+learning_rate', [0.003045598318716111]),
                'da+learning_rate_decay_factor'  : hp.choice('da+learning_rate_decay_factor', [0.6129714547274026]),
                'da+minimum_learning_rate'       : hp.choice('da+minimum_learning_rate', [1.0010053206086294e-7]),
                'da+weight_decay'                : hp.choice('da+weight_decay', [1.4042066931387e-7]),
                'da+clip_gradient'               : hp.choice('da+clip_gradient', [10]),
            },

#            {
#                'type'                           : 'DeepStateEstimator',
#                'ds_cell_type'                   : hp.choice('ds_cell_type', ['lstm', 'gru']),
#                'add_trend'                      : hp.choice('add_trend', [True, False]),
#                'ds_num_cells'                   : hp.choice('ds_num_cells', [2, 4, 8, 16, 32, 64, 128, 256, 512]),
#                'ds_num_layers'                  : hp.choice('ds_num_layers', [1, 2, 3, 4, 5, 7, 9]),
#                'num_periods_to_train'           : hp.choice('num_periods_to_train', [2, 3, 4, 5, 6]),   
#                'ds_dropout_rate'                : hp.uniform('ds_dropout_rate', dropout_rate['min'], dropout_rate['max']),
#                
#                'ds+max_epochs'                  : hp.choice('ds+max_epochs', max_epochs),
#                'ds+num_batches_per_epoch'       : hp.choice('ds+num_batches_per_epoch', num_batches_per_epoch),
#                'ds+batch_size'                  : hp.choice('ds+batch_size', batch_size),
#                'ds+patience'                    : hp.choice('ds+patience', patience),
#                
#                'ds+learning_rate'               : hp.loguniform('ds+learning_rate', learning_rate['min'], learning_rate['max']),
#                'ds+learning_rate_decay_factor'  : hp.uniform('ds+learning_rate_decay_factor', learning_rate_decay_factor['min'], learning_rate_decay_factor['max']),
#                'ds+minimum_learning_rate'       : hp.loguniform('ds+minimum_learning_rate', minimum_learning_rate['min'], minimum_learning_rate['max']),
#                'ds+weight_decay'                : hp.loguniform('ds+weight_decay', weight_decay['min'], weight_decay['max']),
#                'ds+clip_gradient'               : hp.uniform('ds+clip_gradient', clip_gradient['min'], clip_gradient['max']),
#            },
        ])
    }
                            
    if use_cluster:
        exp_key = "%s" % str(date.today())
        logger.info("exp_key for this job is: %s" % exp_key)
        trials = MongoTrials('mongo://heika:27017/%s-%s/jobs' % (dataset_name, version), exp_key=exp_key)
        best = fmin(gluonts_fcast, space, rstate=np.random.RandomState(42), algo=rand.suggest, show_progressbar=False, trials=trials, max_evals=1000)
    else:
        best = fmin(gluonts_fcast, space, algo=rand.suggest, show_progressbar=False, max_evals=20)
         
    return space_eval(space, best) 
    
if __name__ == "__main__":
    params = call_hyperopt()
    logger.info("Best params:\n%s" % pformat(params, indent=4, width=160))
