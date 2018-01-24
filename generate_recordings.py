'''

'''


import numpy as np
import os, sys
from os.path import join
import matplotlib.pyplot as plt
import elephant
import scipy.signal as ss
import scipy.stats as stats
import quantities as pq
import json
import yaml
import time
import multiprocessing
from copy import copy

import spiketrain_generator as stg
from tools import *
from neuroplot import *

root_folder = os.getcwd()

#TODO add bursting events

class GenST:
    def __init__(self, save=False, spike_folder=None, fs=None, noise_mode=None, n_cells=None, p_exc=None,
                 bound_x=None, min_amp=None, noise_level=None, duration=None, f_exc=None, f_inh=None,
                 filter=True, over=None, sync=None, modulation=True, min_dist=None, plot_figures=True,
                 seed=2308):

        self.seed = seed
        np.random.seed(seed)

        self.spike_folder = spike_folder
        self.fs = float(fs) * pq.kHz
        self.n_cells = int(n_cells)
        self.p_exc = float(p_exc)
        self.p_inh = 1-p_exc
        self.bound_x = bound_x
        self.min_amp = float(min_amp)
        self.min_dist = float(min_dist)
        self.noise_mode = noise_mode # 'uncorrelated' - 'correlated-dist' #
        self.noise_level = float(noise_level)
        self.duration = float(duration)*pq.s
        self.f_exc = float(f_exc)*pq.Hz
        self.f_inh = float(f_inh)*pq.Hz
        self.filter = filter
        self.overlap_threshold = float(over)
        self.sync_rate = float(sync)
        self.spike_modulation=modulation
        self.save=save
        self.plot_figures = plot_figures

        parallel=True

        print 'Loading spikes and MEA'
        val_folder = join(os.getcwd(), self.spike_folder, 'validation_data')
        spikes, _, loc, rot, cat = load_validation_data(val_folder)

        all_categories = ['BP', 'BTC', 'ChC', 'DBC', 'LBC', 'MC', 'NBC',
                          'NGC', 'SBC', 'STPC', 'TTPC1', 'TTPC2', 'UTPC']

        cell_dict = {}
        for cc in all_categories:
            cell_dict.update({int(np.argwhere(np.array(all_categories) == cc)): cc})
        cat = np.array([cell_dict[cc] for cc in cat])

        exc_categories = ['STPC', 'TTPC1', 'TTPC2', 'UTPC']
        inh_categories = ['BP', 'BTC', 'ChC', 'DBC', 'LBC', 'MC', 'NBC', 'NGC', 'SBC']
        bin_cat = get_binary_cat(cat, exc_categories, inh_categories)

        # open 1 yaml file and save pitch
        yaml_files = [f for f in os.listdir(self.spike_folder) if '.yaml' in f or '.yml' in f]
        with open(join(self.spike_folder, yaml_files[0]), 'r') as f:
            self.info = yaml.load(f)

        self.electrode_name = self.info['General']['electrode name']
        self.rotation_type = self.info['General']['rotation']
        self.n_points = self.info['General']['n_points']

        # load MEA info
        with open(join(root_folder, 'electrodes', self.electrode_name + '.json')) as meafile:
            elinfo = json.load(meafile)

        x_plane = 0.
        pos = MEA.get_elcoords(x_plane, **elinfo)

        x_plane = 0.
        self.mea_pos = MEA.get_elcoords(x_plane, **elinfo)
        self.mea_pitch = elinfo['pitch']
        self.mea_dim = elinfo['dim']
        mea_shape=elinfo['shape']

        n_elec = spikes.shape[1]
        # this is fixed from recordings
        spike_duration = 7*pq.ms
        spike_fs = 32*pq.kHz

        print 'Selecting cells'
        n_exc = int(self.p_exc*self.n_cells)
        n_inh = self.n_cells - n_exc
        print n_exc, ' excitatory and ', n_inh, ' inhibitory'

        idxs_cells = select_cells(loc, spikes, bin_cat, n_exc, n_inh, bound_x=self.bound_x, min_amp=self.min_amp,
                                  min_dist=self.min_dist)
        self.templates_cat = cat[idxs_cells]
        self.templates_loc = loc[idxs_cells]
        self.templates_bin = bin_cat[idxs_cells]
        templates = spikes[idxs_cells]

        if self.plot_figures:
            plot_mea_recording(templates[0], self.mea_pos, self.mea_dim, color='r')


        up = self.fs
        down = spike_fs
        sampling_ratio = float(up/down)
        # resample spikes
        self.resample=False
        self.pad_len = [3, 3] * pq.ms
        pad_samples = [int((pp*self.fs).magnitude) for pp in self.pad_len]
        n_resample = int((self.fs * spike_duration).magnitude)
        if templates.shape[2] != n_resample:
            templates_pol = np.zeros((templates.shape[0], templates.shape[1], n_resample))
            print 'Resampling spikes'
            for t, tem in enumerate(templates):
                tem_pad = np.pad(tem, [(0,0), pad_samples], 'edge')
                tem_poly = ss.resample_poly(tem_pad, up, down, axis=1)
                templates_pol[t, :] = tem_poly[:, int(sampling_ratio*pad_samples[0]):int(sampling_ratio*pad_samples[0])
                                                                                     +n_resample]
            self.resample=True
        else:
            templates_pol = templates

        templates_pad = []
        templates_spl = []
        print 'Padding edges'
        for t, tem in enumerate(templates_pol):
            tem, spl = cubic_padding(tem, self.pad_len, self.fs)
            templates_pad.append(tem)
            templates_spl.append(spl)


        self.templates = np.array(templates_pad)
        self.splines = np.array(templates_spl)


        if self.plot_figures:
            plot_mea_recording(self.templates[0], self.mea_pos, self.mea_dim)



        print 'Generating spiketrains'
        print self.duration
        self.spgen = stg.SpikeTrainGenerator(n_exc=n_exc, n_inh=n_inh, f_exc=self.f_exc, f_inh=self.f_inh,
                                             st_exc=1*pq.Hz, st_inh=2*pq.Hz, ref_period=5*pq.ms,
                                             process='poisson', t_start=0*pq.s, t_stop=self.duration)

        self.spgen.generate_spikes()
        if self.plot_figures:
            ax = self.spgen.raster_plots()
            ax.set_title('Before synchrony')

        spike_matrix = self.spgen.resample_spiketrains(fs=self.fs)
        n_samples = spike_matrix.shape[1]

        print 'Adding synchrony on overlapping spikes'
        self.overlapping = find_overlapping_spikes(self.templates, thresh=self.overlap_threshold)

        for over in self.overlapping:
            self.spgen.add_synchrony(over, rate=self.sync_rate)

        #TODO generate bursting events in spiketrain_generator
        # if self.bursting > 0:
        #     for i, st in enumerate(self.spgen.all_spiketrains):
        #         rand = np.random.rand()
        #         if rand < self.bursting:
        #             st.annotate(bursting=True)
        #             st = bursting_st(freq=st.annotations['freq'])


        if self.plot_figures:
            ax = self.spgen.raster_plots()
            ax.set_title('After synchrony')

        print 'Adding spiketrain annotations'
        for i, st in enumerate(self.spgen.all_spiketrains):
            st.annotate(bintype=self.templates_bin[i], mtype=self.templates_cat[i], loc=self.templates_loc[i])
        print 'Finding overlapping spikes'
        annotate_overlapping(self.spgen.all_spiketrains, overlapping_pairs=self.overlapping, verbose=True)


        self.amp_mod = []
        self.cons_spikes = []
        self.mrand = 1
        self.sdrand = 0.05
        self.exp = 0.3
        self.n_isi = 5
        self.mem_isi = 10*pq.ms
        if self.spike_modulation == 'all':
            for st in self.spgen.all_spiketrains:
                amp, cons = ISI_amplitude_modulation(st, mrand=self.mrand, sdrand=self.sdrand,
                                                     n_spikes=self.n_isi, exp=self.exp, mem_ISI=self.mem_isi)
                self.amp_mod.append(amp)
                self.cons_spikes.append(cons)
        elif self.spike_modulation == 'noise':
            for st in self.spgen.all_spiketrains:
                amp, cons = ISI_amplitude_modulation(st, mrand=self.mrand, sdrand=self.sdrand,
                                                     n_spikes=0, exp=self.exp, mem_ISI=self.mem_isi)
                self.amp_mod.append(amp)
                self.cons_spikes.append(cons)
        print 'Generating clean recordings'
        self.recordings = np.zeros((n_elec, n_samples))

        # modulated convolution
        # TODO add multiprocessing
        pool = multiprocessing.Pool(n_cells)
        t_start = time.time()

        if not parallel:
            for st, spike_bin in enumerate(spike_matrix):
                print 'Convolving with spike ', st, ' out of ', spike_matrix.shape[0]
                if self.spike_modulation == 'none':
                    self.recordings += convolve_templates_spiketrains(spike_bin, templates[st])
                else:
                    self.recordings += convolve_templates_spiketrains(spike_bin, templates[st], modulation=True,
                                                                      amp_mod=self.amp_mod[st])
        else:
            if self.spike_modulation == 'none':
                results = [pool.apply_async(convolve_templates_spiketrains, (spike_bin, templates[st],))
                           for st, spike_bin in enumerate(spike_matrix)]
            else:
                results = [pool.apply_async(convolve_templates_spiketrains, (st, spike_bin, templates[st], True, amp))
                           for st, (spike_bin, amp) in enumerate(zip(spike_matrix, self.amp_mod))]
            for r in results:
                self.recordings += r.get()

        pool.close()

        print 'Elapsed time ', time.time() - t_start

        self.clean_recordings = copy(self.recordings)

        print 'Adding noise'
        if self.noise_level > 0:
            if noise_mode == 'uncorrelated':
                self.additive_noise = self.noise_level * np.random.randn(self.recordings.shape[0], self.recordings.shape[1])
                self.recordings += self.additive_noise
            elif noise_mode == 'correlated-dist':
                # TODO divide in chunks
                cov_dist = np.zeros((n_elec, n_elec))
                for i, el in enumerate(mea_pos):
                    for j, p in enumerate(mea_pos):
                        if i != j:
                            cov_dist[i, j] = (0.5*np.min(mea_pitch))/np.linalg.norm(el - p)
                        else:
                            cov_dist[i, j] = 1

                self.additive_noise = np.random.multivariate_normal(np.zeros(n_elec), cov_dist,
                                                               size=(self.recordings.shape[0], self.recordings.shape[1]))
                self.recordings += self.additive_noise
        else:
            print 'Noise level is seto to 0'

        if self.filter:
            print 'Filtering signals'
            self.bp = [300, 6000]*pq.Hz
            if self.fs/2. < self.bp[1]:
                self.recordings = filter_analog_signals(self.recordings, freq=self.bp[0], fs=self.fs,
                                                        filter_type='highpass')
            else:
                self.recordings = filter_analog_signals(self.recordings, freq=self.bp, fs=self.fs)
            self.times = np.arange(self.recordings.shape[1])/self.fs
            if self.plot_figures:
                plot_mea_recording(self.recordings, self.mea_pos, self.mea_pitch, color='g')

        if self.save:
            self.save_spikes()


    def save_spikes(self):
        ''' save meta data in old fashioned format'''
        if self.spike_modulation:
            self.rec_name = 'recording_' + self.rotation_type + '_' + self.electrode_name + '_' + str(self.n_cells) \
                            + '_' + str(self.duration).replace(' ','') + '_' + self.noise_mode + '_' \
                            + str(self.noise_level) + '_' + str(self.f_exc).replace(' ','') + '_' \
                            + str(self.f_inh).replace(' ','') + '_modulated' \
                            + '_' + time.strftime("%d-%m-%Y:%H:%M")
        else:
            self.rec_name = 'recording_' + self.rotation_type + '_' + self.electrode_name + '_' + str(self.n_cells) \
                            + '_' + str(self.duration).replace(' ', '') + '_' + self.noise_mode + '_' \
                            + str(self.noise_level) + '_' + str(self.f_exc).replace(' ', '') + '_' \
                            + str(self.f_inh).replace(' ', '') + '_nonmodulated' \
                            + '_' + time.strftime("%d-%m-%Y:%H:%M")
        rec_dir = join(root_folder, 'recordings', 'convolution')
        self.rec_path = join(rec_dir, self.rec_name)
        os.makedirs(self.rec_path)
        # Save meta_info yaml
        with open(join(self.rec_path, 'rec_info.yaml'), 'w') as f:

            # ipdb.set_trace()
            general = {'spike_folder': self.spike_folder, 'rotation': self.rotation_type,
                       'pitch': self.mea_pitch, 'electrode name': str(self.electrode_name),
                       'MEA dimension': self.mea_dim, 'fs': self.fs, 'duration': str(self.duration),
                       'seed': self.seed}

            templates = {'pad_len': str(self.pad_len)}

            spikegen = {'duration': str(self.duration),
                        'f_exc': str(self.f_exc), 'f_inh': str(self.f_inh),
                        'p_exc': round(self.p_exc,4), 'p_inh': round(self.p_inh, 4), 'n_cells': self.n_cells,
                        'bound_x': self.bound_x, 'min_amp': self.min_amp, 'min_dist': self.min_dist}

            synchrony = {'overlap_threshold': self.overlap_threshold,
                         'overlap_pairs_str': str([list(ov) for ov in self.overlapping]),
                         'overlap_pairs': self.overlapping,
                         'sync_rate': self.sync_rate}

            modulation = {'modulation': self.spike_modulation,
                          'mrand': self.mrand, 'sdrand': self.sdrand,
                          'n_isi': self.n_isi, 'exp': self.exp,
                          'mem_ISI': str(self.mem_isi)
                          }

            noise = {'noise_mode': self.noise_mode, 'noise_level': self.noise_level}

            filter = {'filter': self.filter, 'bp': str(self.bp)}


            # create dictionary for yaml file
            data_yaml = {'General': general,
                         'Templates': templates,
                         'Spikegen': spikegen,
                         'Synchrony': synchrony,
                         'Modulation': modulation,
                         'Filter': filter,
                         'Noise': noise
                         }

            yaml.dump(data_yaml, f, default_flow_style=False)

        np.save(join(self.rec_path,'recordings'), self.recordings)
        np.save(join(self.rec_path,'spiketrains'), self.spgen.all_spiketrains)
        np.save(join(self.rec_path,'templates'), self.templates)
        np.save(join(self.rec_path,'templates_loc'), self.templates_loc)
        np.save(join(self.rec_path,'templates_cat'), self.templates_cat)
        np.save(join(self.rec_path,'templates_bincat'), self.templates_cat)

        print 'Saved ', self.rec_path

def convolve_templates_spiketrains(spike_id, spike_bin, template, modulation=False, amp_mod=None):
    print 'START: convolution with spike ', spike_id
    n_elec = template.shape[0]
    n_samples = len(spike_bin)
    recordings = np.zeros((n_elec, n_samples))
    if not modulation:
        for el in range(n_elec):
            recordings[el] += np.convolve(spike_bin, template[el], mode='same')
    else:
        assert amp_mod is not None
        spike_pos = np.where(spike_bin == 1)[0]
        for pos, spos in enumerate(spike_pos):
            single_spike_array = np.zeros_like(spike_bin)
            single_spike_array[spos] = 1
            for el in range(n_elec):
                recordings[el] += np.convolve(single_spike_array,
                                              amp_mod[pos] * template[el], mode='same')
    print 'DONE: convolution with spike ', spike_id
    return recordings


if __name__ == '__main__':
    '''
        COMMAND-LINE 
        -f filename
        -fs sampling frequency
        -ncells number of cells
        -pexc proportion of exc cells
        -bx x boundaries
        -minamp minimum amplitude
        -noise uncorrelated-correlated
        -noiselev level of rms noise in uV
        -dur duration
        -fexc freq exc neurons
        -finh freq inh neurons
        -nofilter if filter or not
        -over overlapping spike threshold (0.6)
        -sync added synchorny rate'
    '''

    if '-f' in sys.argv:
        pos = sys.argv.index('-f')
        spike_folder = sys.argv[pos + 1]
    if '-freq' in sys.argv:
        pos = sys.argv.index('-freq')
        freq = sys.argv[pos + 1]
    else:
        freq = 32
    if '-dur' in sys.argv:
        pos = sys.argv.index('-dur')
        dur = sys.argv[pos + 1]
    else:
        dur = 5
    if '-ncells' in sys.argv:
        pos = sys.argv.index('-ncells')
        ncells = int(sys.argv[pos + 1])
    else:
        ncells = 30
    if '-pexc' in sys.argv:
        pos = sys.argv.index('-pexc')
        pexc = sys.argv[pos + 1]
    else:
        pexc = 0.7
    if '-fexc' in sys.argv:
        pos = sys.argv.index('-fexc')
        fexc = sys.argv[pos + 1]
    else:
        fexc = 5
    if '-finh' in sys.argv:
        pos = sys.argv.index('-finh')
        finh = sys.argv[pos + 1]
    else:
        finh = 15
    if '-bx' in sys.argv:
        pos = sys.argv.index('-bx')
        bx = sys.argv[pos + 1]
    else:
        bx = [20,60]
    if '-minamp' in sys.argv:
        pos = sys.argv.index('-minamp')
        minamp = sys.argv[pos+1]
    else:
        minamp = 50
    if '-mindist' in sys.argv:
        pos = sys.argv.index('-mindist')
        mindist = sys.argv[pos+1]
    else:
        mindist = 25
    if '-over' in sys.argv:
        pos = sys.argv.index('-over')
        over = sys.argv[pos+1]
    else:
        over = 0.6
    if '-sync' in sys.argv:
        pos = sys.argv.index('-sync')
        sync = sys.argv[pos + 1]
    else:
        sync = 0.5
    if '-noise' in sys.argv:
        pos = sys.argv.index('-noise')
        noise = sys.argv[pos+1]
    else:
        noise = 'uncorrelated'
    if '-noiselev' in sys.argv:
        pos = sys.argv.index('-noiselev')
        noiselev = sys.argv[pos+1]
        print noiselev
    else:
        noiselev = 2.6
    if '-nofilter' in sys.argv:
        filter=False
    else:
        filter=True
    if '-nomod' in sys.argv:
        modulation='none'
    else:
        modulation='all'
    if '-noplot' in sys.argv:
        plot_figures=False
    else:
        plot_figures=True
    if '-noisemod' in sys.argv:
        modulation='noise'
    if '-seed' in sys.argv:
        pos = sys.argv.index('-seed')
        seed = sys.argv[pos + 1]
    else:
        seed=2308

    if len(sys.argv) == 1:
        print 'Arguments: \n   -f filename\n   -dur duration\n   -freq sampling frequency (in kHz)\n   ' \
              '-ncells number of cells\n' \
              '   -pexc proportion of exc cells\n   -bx x boundaries [xmin,xmax]\n   -minamp minimum amplitude\n' \
              '   -noise uncorrelated-correlated\n   -noiselev level of rms noise in uV\n   -dur duration\n' \
              '   -fexc freq exc neurons\n   -finh freq inh neurons\n   -nofilter if filter or not\n' \
              '   -over overlapping spike threshold (0.6)\n   -sync added synchorny rate\n' \
              '   -nomod no spike amp modulation'
    elif '-f' not in sys.argv:
        raise AttributeError('Provide model folder for data')
    else:
        gs = GenST(save=True, spike_folder=spike_folder, fs=freq, n_cells=ncells, p_exc=pexc, duration=dur,
                   bound_x=bx, min_amp=minamp, noise_mode=noise, noise_level=noiselev, f_exc=fexc, f_inh=finh,
                   filter=filter, over=over, sync=sync, modulation=modulation, min_dist=mindist, 
                   plot_figures=plot_figures, seed=seed)





