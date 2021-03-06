import numpy as np
import os, sys
from os.path import join
import matplotlib.pyplot as plt
import neo
import elephant
import scipy.signal as ss
import scipy.stats as stats
import scipy.io as sio
import quantities as pq
import json
import yaml
import time
import h5py
import ipdb
import mne
import pandas as pd
import seaborn as sns

from tools import *
from neuroplot import *
import MEAutility as MEA
from spike_sorting import plot_mixing, plot_templates, templates_weights
import ICA as ica
import orICA as orica
from picard import picard

root_folder = os.getcwd()
plt.ion()
plt.show()

save_results = False


def plot_topo_eeg(a, pos):
    '''

    Parameters
    ----------
    a
    pos

    Returns
    -------

    '''
    from mne.viz import plot_topomap

    n_sources = len(a)
    cols = int(np.ceil(np.sqrt(n_sources)))
    rows = int(np.ceil(n_sources / float(cols)))
    fig_t = plt.figure()

    for n in range(n_sources):
        ax_w = fig_t.add_subplot(rows, cols, n + 1)
        mix = mixing[n] / np.ptp(mixing[n])
        plot_topomap(a[n], pos, axes=ax_w)

    return fig_t


if __name__ == '__main__':
    if '-r' in sys.argv:
        pos = sys.argv.index('-r')
        folder = sys.argv[pos + 1]
    if '-mod' in sys.argv:
        pos = sys.argv.index('-mod')
        mod = sys.argv[pos + 1]
    else:
        mod = 'orica'
    if '-block' in sys.argv:
        pos = sys.argv.index('-block')
        block_size = int(sys.argv[pos + 1])
    else:
        block_size = 500
    if '-ff' in sys.argv:
        pos = sys.argv.index('-ff')
        ff = sys.argv[pos + 1]
    else:
        ff = 'cooling'
    if '-lambda' in sys.argv:
        pos = sys.argv.index('-lambda')
        lambda_v = sys.argv[pos + 1]
    else:
        lambda_v = 0.995
    if '-oricamod' in sys.argv:
        pos = sys.argv.index('-oricamod')
        oricamod = sys.argv[pos + 1]
    else:
        oricamod = 'original'
    if '-npass' in sys.argv:
        pos = sys.argv.index('-npass')
        npass = int(sys.argv[pos + 1])
    else:
        npass = 1
    if '-reg' in sys.argv:
        pos = sys.argv.index('-reg')
        reg = sys.argv[pos + 1]
    else:
        reg = 'L2'
    if '-mu' in sys.argv:
        pos = sys.argv.index('-mu')
        mu = float(sys.argv[pos + 1])
    else:
        mu = 0
    if '-M' in sys.argv:
        pos = sys.argv.index('-M')
        ndim = int(sys.argv[pos + 1])
    else:
        ndim = 'all'
    if '-nowhiten' in sys.argv:
        whiten = False
    else:
        whiten = True
    if '-noortho' in sys.argv:
        ortho = False
    else:
        ortho = True
    if '-noplot' in sys.argv:
        plot_fig = False
    else:
        plot_fig = True
    if '-online' in sys.argv:
        onlinesphering = True
        return_evolution = True
    else:
        onlinesphering = False
        return_evolution = False

    if '-resfile' in sys.argv:
        pos = sys.argv.index('-resfile')
        resfile = sys.argv[pos + 1]
    else:
        resfile = 'results.csv'

    debug = False

    if len(sys.argv) == 1 and not debug:
        print 'Evaluate ICA for spike sorting:\n   -r recording folder\n   -mod orica-ica\n   \nblock block size' \
              '\n   -ff constant-cooling\n   -mu smoothing\n   -lambda lambda_0' \
              '\n   -oricamod  original - A - W - A_block - W_block\n   -npass numpass\n'
        # raise Exception('Indicate recording folder -r')
        folder = 'recordings/recording_eeg_16chan_ica.mat'
        block_size = 10
        ff = 'cooling'
        mu = 0.1
        oricamod = 'W_block'
        raise Exception('Arguments!')


    if debug:
        folder = '/home/alessio/Documents/Codes/SpyICA/recordings/recording_eeg_16chan_ica.mat'
        block_size = 100
        onlinesphering=True

    folder = os.path.abspath(folder)
    print folder

    # else:
    if 'eeg' not in folder:
        recordings = np.load(join(folder, 'recordings.npy')).astype('int16')
        rec_info = [f for f in os.listdir(folder) if '.yaml' in f or '.yml' in f][0]
        with open(join(folder, rec_info), 'r') as f:
            info = yaml.load(f)
        electrode_name = info['General']['electrode name']
        mea_pos, mea_dim, mea_pitch = MEA.return_mea(electrode_name)

        templates = np.load(join(folder, 'templates.npy'))
        mixing = np.load(join(folder, 'mixing.npy')).T
        sources = np.load(join(folder, 'sources.npy'))
        gtst = np.load(join(folder, 'spiketrains.npy'))

        adj_graph = extract_adjacency(mea_pos, np.max(mea_pitch) + 5)

        n_sources = sources.shape[0]
    else:
        mat_contents = sio.loadmat(folder)
        recordings = mat_contents['recordings']
        mixing = mat_contents['mixing']
        times = mat_contents['times']
        sources = mat_contents['sources']
        mea_pos = mat_contents['pos']
        mea_pitch = [10, 10]
        mea_dim = [4, 4]
        adj_graph = extract_adjacency(mea_pos, 0.06)

        n_sources = sources.shape[0]


    orica_type = oricamod # original - W - A -  W_block - A_block
    # if ff == 'constant':
    if lambda_v == 'N':
        lambda_val = 1. / recordings.shape[1] # 0.995
    else:
        lambda_val = float(lambda_v)

    t_start = time.time()
    if mod == 'orica':
        if orica_type == 'original':
            ori = orica.ORICA(recordings, onlineWhitening=onlinesphering, forgetfac=ff, lambda_0=lambda_val,
                              mu=mu, verbose=True, numpass=npass, block_white=block_size, block_ica=block_size,
                              adjacency=adj_graph, whiten=whiten, ortho=ortho, ndim=ndim, white_mode='pca')
        elif orica_type == 'W':
            ori = orica.ORICA_W(recordings, sphering='offline', forgetfac=ff, lambda_0=lambda_val,
                                mu=mu, verbose=True, numpass=1, block_white=block_size, block_ica=block_size,
                                adjacency=adj_graph)
        elif orica_type == 'A':
            ori = orica.ORICA_A(recordings, sphering='offline', forgetfac=ff, lambda_0=lambda_val,
                                mu=mu, verbose=True, numpass=npass, block_white=block_size, block_ica=block_size,
                                adjacency=adj_graph)
        elif orica_type == 'W_block':
            ori = orica.ORICA_W_block(recordings, sphering='offline', forgetfac=ff, lambda_0=lambda_val,
                                      mu=mu, verbose=True, numpass=npass, block_white=block_size, block_ica=block_size,
                                      adjacency=adj_graph, regmode=reg)
        elif orica_type == 'A_block':
            ori = orica.ORICA_A_block(recordings, sphering='offline', forgetfac=ff, lambda_0=lambda_val,
                                      mu=mu, verbose=True, numpass=npass, block_white=block_size, block_ica=block_size,
                                      adjacency=adj_graph, regmode=reg)
        else:
            raise Exception('ORICA type not understood')

        # if not return_evolution:
        y = ori.y
        w = ori.unmixing
        m = ori.sphere
        a = ori.mixing
        # else:
        #     y = ori.y
        #     w = ori.unmixing[-1]
        #     m = ori.sphere[-1]
        #     a = ori.mixing[-1]

    elif mod == 'ica':
        oricamod = '-'
        y, a, w = ica.instICA(recordings, n_comp=ndim)
    elif mod == 'picard':
        oricamod = '-'
        m, w_, y = picard(recordings, ortho=False)
        w = np.matmul(w_, m)
        a = np.linalg.inv(w)
    elif mod == 'picard-o':
        oricamod = '-'
        m, w_, y = picard(recordings, ortho=True)
        w = np.matmul(w_, m)
        a = np.linalg.inv(w)

    proc_time = time.time() - t_start
    print 'Processing time: ', proc_time

    if 'eeg' not in folder:
        # Skewness
        skew_thresh = 0.1
        sk = stats.skew(y, axis=1)
        high_sk = np.where(np.abs(sk) >= skew_thresh)

        # Kurtosis
        ku_thresh = 0.8
        ku = stats.kurtosis(y, axis=1)
        high_ku = np.where(np.abs(ku) >= ku_thresh)

        a_t = np.zeros(a.shape)
        for i, a_ in enumerate(a):
            if np.abs(np.min(a_)) > np.abs(np.max(a_)):
                a_t[i] = -a_ / np.max(np.abs(a_))
            else:
                a_t[i] = a_ / np.max(np.abs(a_))

        # Smoothness
        smooth = np.zeros(a.shape[0])
        for i in range(len(smooth)):
           smooth[i] = (np.mean([1. / len(adj) * np.sum(a_t[i, j] - a_t[i, adj]) ** 2
                                                 for j, adj in enumerate(adj_graph)]))
        if len(high_sk) != 0:
            n_high_sk = len(high_sk[0])
        if len(high_ku) != 0:
            n_high_ku = len(high_ku[0])

        correlation, idx_truth, idx_orica, _ = matcorr(mixing.T, a[high_sk])
        sorted_idx = idx_orica[idx_truth.argsort()]
        sorted_corr_idx = idx_truth.argsort()
        # high_sk_a = a[high_sk]
        sorted_a = np.matmul(np.diag(np.sign(correlation[sorted_corr_idx])), a[high_sk][sorted_idx]).T
        sorted_mixing = mixing[:, np.sort(idx_truth)]
        sorted_y = np.matmul(np.diag(np.sign(correlation[sorted_corr_idx])), y[high_sk][sorted_idx])
        sorted_y_true = sources[np.sort(idx_truth)]
        average_corr = np.mean(np.abs(correlation[sorted_corr_idx]))

        # PI, C = evaluate_PI(sorted_a.T, sorted_mixing)
        mix_CC_mean_gt, mix_CC_mean_id, sources_CC_mean, \
        corr_cross_mix, corr_cross_sources = evaluate_sum_CC(sorted_a.T, sorted_mixing.T, sorted_y_true, sorted_y,
                                                             n_sources)

        if plot_fig:
            plot_mixing(sorted_mixing.T, mea_dim)
            plot_mixing(sorted_a.T, mea_dim)

            # fig = plt.figure()
            # ax = fig.add_subplot(111)
            # colors = plt.rcParams['axes.color_cycle']
            # for i, (true_y, ica_y) in enumerate(zip(sorted_y_true, sorted_y)):
            #     ax.plot(true_y/np.max(np.abs(true_y)), alpha=0.3, color=colors[int(np.mod(i, len(colors)))])
            #     ax.plot(ica_y/np.max(np.abs(ica_y)), alpha=0.8, color=colors[int(np.mod(i, len(colors)))])

    else:
        a_t = np.zeros(a.shape)
        for i, a_ in enumerate(a):
            if np.abs(np.min(a_)) > np.abs(np.max(a_)):
                a_t[i] = -a_ / np.max(np.abs(a_))
            else:
                a_t[i] = a_ / np.max(np.abs(a_))

        # Smoothness (A is transpose already)
        smooth = np.zeros(a.shape[0])
        for i in range(len(smooth)):
            smooth[i] = np.sum([1. / len(adj) * np.sum(a_t[i, j] - a_t[i, adj]) ** 2
                                for j, adj in enumerate(adj_graph)])

        correlation, idx_truth, idx_orica, _ = matcorr(mixing.T, a)
        sorted_idx = idx_orica[idx_truth.argsort()]
        sorted_corr_idx = idx_truth.argsort()
        sorted_a = np.matmul(np.diag(np.sign(correlation[sorted_corr_idx])), a[sorted_idx]).T
        sorted_mixing = mixing[:, np.sort(idx_truth)]
        sorted_y = np.matmul(np.diag(np.sign(correlation[sorted_corr_idx])), y[sorted_idx])
        sorted_y_true = sources[np.sort(idx_truth)]
        average_corr = np.mean(np.abs(correlation[sorted_corr_idx]))

        # PI, C = evaluate_PI(w, mixing)
        mix_CC_mean_gt, mix_CC_mean_id, sources_CC_mean, \
        corr_cross_mix, corr_cross_sources = evaluate_sum_CC(sorted_a.T, sorted_mixing.T, sorted_y_true, sorted_y,
                                                             n_sources)

        if plot_fig:
            plot_topo_eeg(mixing.T, mea_pos)
            plot_topo_eeg(sorted_a.T, mea_pos)

    # print 'Average smoothing: ', np.mean(smooth)
    # print 'Performance index: ', PI
    # print 'Average correlation: ', average_corr
    print 'Number of GT sources: ', n_sources
    print 'Number of identified sources: ', n_high_sk
    print 'Normalized cumulative correlation GT - mixing: ', mix_CC_mean_gt
    print 'Normalized cumulative correlation ID - mixing: ', mix_CC_mean_id
    print 'Normalized cumulative correlation - sources: ', sources_CC_mean

    # cc_sources = np.dot(sources, y.T)
    # cc_mixing = np.dot(mixing.T, a.T)

    if save_results and 'eeg' not in folder:
        if not os.path.isfile(join(folder, resfile)):
            df = pd.DataFrame({'mu': [mu], 'numpass': [npass], 'reg': [reg], 'oricamode': [oricamod], 'mod': [mod],
                               'block': [block_size], 'ff': [ff], 'lambda': [lambda_v], 'time': [proc_time],
                               'CC_mix': [mix_CC_mean], 'CC_source': [sources_CC_mean], 'n_sk': [n_high_sk],
                               'n_ku': [n_high_ku]})
            print 'Saving to ', join(folder, resfile)
            with open(join(folder, resfile), 'w') as f:
                df.to_csv(f)
        else:
            with open(join(folder, resfile), 'r') as f:
                new_index = len(pd.read_csv(f))
            with open(join(folder, resfile), 'a') as f:
                df = pd.DataFrame({'mu': [mu], 'numpass': [npass], 'reg': [reg], 'oricamode': [oricamod], 'mod': [mod],
                                   'block': [block_size], 'ff': [ff], 'lambda': [lambda_v], 'time': [proc_time],
                                   'CC_mix': [mix_CC_mean], 'CC_source': [sources_CC_mean], 'n_sk': [n_high_sk],
                                   'n_ku': [n_high_ku]}, index=[new_index])
                print 'Appending to ', join(folder, resfile)
                df.to_csv(f, header=False)

    plt.ion()
    plt.show()


    # colors = plt.rcParams['axes.color_cycle']
    # norm_y = sorted_y / np.max(np.abs(sorted_y), axis=1, keepdims=True)
    # lw = 0.8
    # interval = 64000
    # t_int = 2 * pq.s
    # t_start = sorted_gtst[0].t_stop - t_int
    # t_stop = sorted_gtst[0].t_stop
    #
    # fig_ic, ax_ic = plt.subplots()
    # ax_ic.plot(norm_y[0, -interval:], lw=lw)
    # ax_ic.plot(norm_y[1, -interval:] + 1.3, lw=lw)
    # ax_ic.plot(norm_y[2, -interval:] + 2.6, lw=lw)
    # ax_ic.axis('off')
    # fig_ic.tight_layout()
    #
    # fig_th, ax_th = plt.subplots()
    # ax_th.plot(norm_y[0, -interval:], lw=lw)
    # ax_th.plot(norm_y[1, -interval:] + 1.3, lw=lw)
    # ax_th.plot(norm_y[2, -interval:] + 2.6, lw=lw)
    # ax_th.axhline(-0.5, color=colors[0], lw=2, ls='--')
    # ax_th.axhline(0.8, color=colors[1], lw=2, ls='--')
    # ax_th.axhline(2, color=colors[2], lw=2, ls='--')
    # ax_th.axis('off')
    # fig_th.tight_layout()
    #
    # fig_mix, ax_mix = plt.subplots(3, 1)
    # plot_weight(sorted_mixing.T[0], mea_dim, ax=ax_mix[0])
    # plot_weight(sorted_mixing.T[1], mea_dim, ax=ax_mix[1])
    # plot_weight(sorted_mixing.T[2], mea_dim, ax=ax_mix[2])
    # fig_mix.tight_layout()
    #
    # fig_kde, ax_kde = plt.subplots()
    # sns.kdeplot(-sorted_y[0], shade=True, ax=ax_kde)
    # sns.kdeplot(-sorted_y[1]-250, shade=True, ax=ax_kde)
    # sns.kdeplot(-sorted_y[2]-500, shade=True, ax=ax_kde)
    # ax_kde.axis('off')
    # fig_kde.tight_layout()
    #
    #
    # fig_st, ax_st = plt.subplots()
    # ax_st.plot(sorted_gtst[0].time_slice(t_start=t_start, t_stop=t_stop),
    #            0*np.ones_like(sorted_gtst[0].time_slice(t_start=t_start, t_stop=t_stop)), '|', markersize=30, mew=2)
    # ax_st.plot(sorted_gtst[1].time_slice(t_start=t_start, t_stop=t_stop),
    #            1*np.ones_like(sorted_gtst[1].time_slice(t_start=t_start, t_stop=t_stop)), '|', markersize=30, mew=2)
    # ax_st.plot(sorted_gtst[2].time_slice(t_start=t_start, t_stop=t_stop),
    #            2*np.ones_like(sorted_gtst[2].time_slice(t_start=t_start, t_stop=t_stop)), '|', markersize=30, mew=2)
    # ax_st.axis('off')
    # fig_st.tight_layout()




    # f = plot_mixing(a[high_sk], mea_pos, mea_dim)
    # f.suptitle('ORICA ' + orica_type + ' ' + str(block_size))
    # plt.figure();
    # plt.plot(y[high_sk].T)
    # plt.title('ORICA ' + orica_type + ' ' + str(block_size))
