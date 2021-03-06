'''
Generate non-stationary recordings in which some units are intermittent (e.g. stimulus response, place cells)
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

#TODO implement intermittent units: 'bursts' of activity only in certain intervals
class GenST:
    def __init__(self, save=False, spike_folder=None, fs=None, noise_mode=None, n_cells=None, n_int_cells=0,
                 f_int=None, t_int=None, t_int_sd=2*pq.s, t_burst=None, t_burst_sd=0.5*pq.s, p_exc=None,
                 bound_x=[], min_amp=None, noise_level=None, duration=None, f_exc=None, f_inh=None,
                 filter=True, over=None, sync=None, modulation='none', min_dist=None, plot_figures=True,
                 seed=2904):
        '''

        Parameters
        ----------
        save: save flag (True-False)
        spike_folder: folder containing spikes or CNN model with validation_data folder
        fs: sampling frequency (if None taken from spikes)
        noise_mode: noise generation (uncorrelated: independent gaussian noise - correlated-dist: noise correlated with distance)
        n_cells: number of cells
        p_exc: percent of excitatory cells
        bound_x: boundaries for x direction in um (e.g. [10 60])
        min_amp: minimum amplitude of templates in uV
        noise_level: rms noise level
        duration: duration in s
        f_exc: average frequency of excitatory cells
        f_inh: average frequency of inhibtory cells
        filter: filter or not (True-False)
        over: threshold to consider 2 templates dpatially overlapping (e.g. 0.6)
        sync: rate of added synchrony on overlapping spikes
        modulation: modulation type (none - noise-all (electrodes modulated separately) - noise (templates modulated separately))
        min_dist: minimum distance between cells in um
        plot_figures: plot figures or not
        seed: random seed to select cells
        '''

        self.seed = seed
        np.random.seed(seed)
        self.chunk_duration = 0 * pq.s
        self.noise_chunk_duration = 2 * pq.s

        self.spike_folder = spike_folder
        self.fs = float(fs) * pq.kHz
        self.n_cells = int(n_cells)
        self.n_int_cells = int(n_int_cells)
        self.t_int = int(t_int) * pq.s
        self.t_burst = int(t_burst) * pq.s
        self.f_int = float(f_int)*pq.Hz
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

        print 'Modulation: ', self.spike_modulation

        # print self.n_add_cells, self.t_add_cells, self.n_remove_cells, self.t_remove_cells

        parallel=False

        all_categories = ['BP', 'BTC', 'ChC', 'DBC', 'LBC', 'MC', 'NBC',
                          'NGC', 'SBC', 'STPC', 'TTPC1', 'TTPC2', 'UTPC']

        exc_categories = ['STPC', 'TTPC1', 'TTPC2', 'UTPC']
        inh_categories = ['BP', 'BTC', 'ChC', 'DBC', 'LBC', 'MC', 'NBC', 'NGC', 'SBC']

        print 'Loading spikes and MEA'
        if os.path.isdir(join(os.getcwd(), self.spike_folder, 'validation_data')):
            val_folder = join(os.getcwd(), self.spike_folder, 'validation_data')
            spikes, _, loc, rot, cat = load_validation_data(val_folder)

            cell_dict = {}
            for cc in all_categories:
                cell_dict.update({int(np.argwhere(np.array(all_categories) == cc)): cc})
            cat = np.array([cell_dict[cc] for cc in cat])
            bin_cat = get_binary_cat(cat, exc_categories, inh_categories)

            # open 1 yaml file and save pitch
            yaml_files = [f for f in os.listdir(self.spike_folder) if '.yaml' in f or '.yml' in f]
            with open(join(self.spike_folder, yaml_files[0]), 'r') as f:
                self.info = yaml.load(f)

            self.electrode_name = self.info['General']['electrode name']
            self.rotation_type = self.info['General']['rotation']
            self.n_points = self.info['General']['n_points']
        else:
            files = [f for f in os.listdir(join(os.getcwd(), self.spike_folder))]
            if any(['e_elpts' in f for f in files]):
                spikes, loc, rot, cat, etype, morphid, loaded_cat = load_EAP_data(self.spike_folder)
            # open 1 yaml file and save pitch
            yaml_files = [f for f in os.listdir(self.spike_folder) if '.yaml' in f or '.yml' in f]
            with open(join(self.spike_folder, yaml_files[0]), 'r') as f:
                self.info = yaml.load(f)

            self.rotation_type = self.info['Location']['rotation']
            self.electrode_name = self.info['Electrodes']['electrode_name']
            self.n_points = self.info['Electrodes']['n_points']


        # load MEA info
        # with open(join(root_folder, 'electrodes', self.electrode_name + '.json')) as meafile:
        #     elinfo = json.load(meafile)
        elinfo = MEA.return_mea_info(self.electrode_name)

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
        n_exc = int(self.p_exc*(self.n_cells))
        n_inh = self.n_cells - n_exc
        print n_exc, ' excitatory and ', n_inh, ' inhibitory'

        idxs_cells = select_cells(loc, spikes, bin_cat, n_exc, n_inh, bound_x=self.bound_x, min_amp=self.min_amp,
                                      min_dist=self.min_dist)

        self.templates_cat = cat[idxs_cells]
        self.templates_loc = loc[idxs_cells]
        self.templates_bin = bin_cat[idxs_cells]
        templates = spikes[idxs_cells]

        # mixing matrices
        mixing = []
        for tem in templates:
            dt = 2**-5
            feat = get_EAP_features(tem, ['Na'],dt=dt)
            mixing.append(-np.squeeze(feat['na']))
        self.mixing = np.array(mixing)

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

        if self.plot_figures:
            plot_templates(np.array(templates_pad), self.mea_pos, self.mea_pitch)

        # sampling jitter
        n_jitters = 10
        upsample = 8
        jitter = 1. / self.fs
        templates_jitter = []
        for temp in templates_pad:
            temp_up = ss.resample_poly(temp, upsample, 1., axis=1)
            nsamples_up = temp_up.shape[1]
            temp_jitt = []
            for n in range(n_jitters):
                # align waveform
                shift = int((jitter * np.random.randn() * upsample * self.fs).magnitude)
                if shift > 0:
                    t_jitt = np.pad(temp_up, [(0, 0), (np.abs(shift), 0)], 'constant')[:, :nsamples_up]
                elif shift < 0:
                    t_jitt = np.pad(temp_up, [(0, 0), (0, np.abs(shift))], 'constant')[:, -nsamples_up:]
                else:
                    t_jitt = temp_up
                temp_down = ss.decimate(t_jitt, upsample, axis=1)
                temp_jitt.append(temp_down)

            templates_jitter.append(temp_jitt)

        templates_jitter = np.array(templates_jitter)

        self.templates = np.array(templates_jitter)
        self.splines = np.array(templates_spl)


        print 'Generating spiketrains'
        print self.duration
        self.spgen = stg.SpikeTrainGenerator(n_exc=n_exc, n_inh=n_inh, f_exc=self.f_exc, f_inh=self.f_inh,
                                             st_exc=1*pq.Hz, st_inh=2*pq.Hz, ref_period=5*pq.ms,
                                             process='poisson', t_start=0*pq.s, t_stop=self.duration,
                                             n_int=self.n_int_cells, f_int=self.f_int,
                                             t_int=self.t_int, t_burst=self.t_burst,
                                             t_int_sd=t_int_sd, t_burst_sd=t_burst_sd)

        self.spgen.generate_spikes()

        if self.plot_figures:
            ax = self.spgen.raster_plots()
            ax.set_title('Before synchrony')

        spike_matrix = self.spgen.resample_spiketrains(fs=self.fs)
        n_samples = spike_matrix.shape[1]

        if self.sync_rate != 0:
            print 'Adding synchrony on overlapping spikes'
            self.overlapping = find_overlapping_spikes(self.templates, thresh=self.overlap_threshold)

            for over in self.overlapping:
                self.spgen.add_synchrony(over, rate=self.sync_rate)
        else:
            self.overlapping = []

        # find SNR and annotate
        print 'Computing SNR'
        for t_i, temp in enumerate(self.templates):
            min_peak = np.min(temp)
            snr = np.abs(min_peak/float(noiselev))
            self.spgen.all_spiketrains[t_i].annotate(snr=snr)
            print min_peak, snr


        if self.plot_figures and self.sync_rate != 0:
            ax = self.spgen.raster_plots()
            ax.set_title('After synchrony')

        print 'Adding spiketrain annotations'
        for i, st in enumerate(self.spgen.all_spiketrains):
            st.annotate(bintype=self.templates_bin[i], mtype=self.templates_cat[i], loc=self.templates_loc[i])
        print 'Finding temporally overlapping spikes'
        annotate_overlapping(self.spgen.all_spiketrains, overlapping_pairs=self.overlapping, verbose=True)


        self.amp_mod = []
        self.cons_spikes = []
        self.mrand = 1
        self.sdrand = 0.05
        self.exp = 0.3
        self.n_isi = 5
        self.mem_isi = 10*pq.ms
        if self.spike_modulation == 'all':
            print 'ISI modulation'
            for st in self.spgen.all_spiketrains:
                amp, cons = ISI_amplitude_modulation(st, mrand=self.mrand, sdrand=self.sdrand,
                                                     n_spikes=self.n_isi, exp=self.exp, mem_ISI=self.mem_isi)
                self.amp_mod.append(amp)
                self.cons_spikes.append(cons)
        elif self.spike_modulation == 'template-all':
            print 'Noisy modulation'
            for st in self.spgen.all_spiketrains:
                amp, cons = ISI_amplitude_modulation(st, mrand=self.mrand, sdrand=self.sdrand,
                                                     n_spikes=0, exp=self.exp, mem_ISI=self.mem_isi)
                self.amp_mod.append(amp)
                self.cons_spikes.append(cons)
        elif self.spike_modulation == 'electrode-all':
            print 'Noisy modulation on all electrodes separately'
            for st in self.spgen.all_spiketrains:
                amp, cons = ISI_amplitude_modulation(st, n_el=n_elec, mrand=self.mrand, sdrand=self.sdrand,
                                                     n_spikes=0, exp=self.exp, mem_ISI=self.mem_isi)
                self.amp_mod.append(amp)
                self.cons_spikes.append(cons)

        print 'Generating clean recordings'
        self.recordings = np.zeros((n_elec, n_samples))
        self.times = np.arange(self.recordings.shape[1]) / self.fs

        # modulated convolution
        pool = multiprocessing.Pool(n_cells)
        t_start = time.time()
        gt_spikes = []

        # divide in chunks
        chunks = []
        if self.duration > self.chunk_duration and self.chunk_duration != 0:
            start = 0 * pq.s
            finished = False
            while not finished:
                chunks.append([start, start + self.chunk_duration])
                start = start + self.chunk_duration
                if start >= self.duration:
                    finished = True
            print 'Chunks: ', chunks

        chunks_noise = []
        if self.duration > self.noise_chunk_duration and self.noise_chunk_duration != 0:
            start = 0 * pq.s
            finished = False
            while not finished:
                chunks_noise.append([start, start + self.noise_chunk_duration])
                start = start + self.noise_chunk_duration
                if start >= self.duration:
                    finished = True
            print 'Chunks noise: ', chunks_noise

        if len(chunks) > 0:
            recording_chunks = []
            for ch, chunk in enumerate(chunks):
                print 'Generating chunk ', ch+1, ' of ', len(chunks)
                idxs = np.where((self.times>=chunk[0]) & (self.times<chunk[1]))[0]
                spike_matrix_chunk = spike_matrix[:, idxs]
                rec_chunk=np.zeros((n_elec, len(idxs)))
                amp_chunk = []
                for i, st in enumerate(self.spgen.all_spiketrains):
                    idxs = np.where((st >= chunk[0]) & (st < chunk[1]))[0]
                    if self.spike_modulation != 'none':
                        amp_chunk.append(self.amp_mod[i][idxs])

                if not parallel:
                    for st, spike_bin in enumerate(spike_matrix_chunk):
                        print 'Convolving with spike ', st, ' out of ', spike_matrix_chunk.shape[0]
                        if self.spike_modulation == 'none':
                            rec_chunk += convolve_templates_spiketrains(st, spike_bin, self.templates[st])
                        else:
                            rec_chunk += convolve_templates_spiketrains(st, spike_bin, self.templates[st],
                                                                                   modulation=True,
                                                                                   amp_mod=amp_chunk[st])
                else:
                    if self.spike_modulation == 'none':
                        results = [pool.apply_async(convolve_templates_spiketrains, (st, spike_bin, self.templates[st],))
                                   for st, spike_bin in enumerate(spike_matrix_chunk)]
                    else:
                        results = [pool.apply_async(convolve_templates_spiketrains,
                                                    (st, spike_bin, self.templates[st], True, amp))
                                   for st, (spike_bin, amp) in enumerate(zip(spike_matrix_chunk, amp_chunk))]
                    for r in results:
                        rec_chunk += r.get()

                recording_chunks.append(rec_chunk)
            self.recordings = np.hstack(recording_chunks)
        else:
            for st, spike_bin in enumerate(spike_matrix):
                print 'Convolving with spike ', st, ' out of ', spike_matrix.shape[0]
                if self.spike_modulation == 'none':
                    # reset random seed to keep sampling of jitter spike same
                    seed = np.random.randint(10000)
                    np.random.seed(seed)

                    self.recordings += convolve_templates_spiketrains(st, spike_bin, self.templates[st])
                    np.random.seed(seed)
                    gt_spikes.append(convolve_single_template(st, spike_bin,
                                                              self.templates[st, :, np.argmax(self.mixing[st])]))
                elif self.spike_modulation == 'electrode-all':
                    seed = np.random.randint(10000)
                    np.random.seed(seed)
                    self.recordings += convolve_templates_spiketrains(st, spike_bin, self.templates[st],
                                                                      modulation=True,
                                                                      amp_mod=self.amp_mod[st])
                    np.random.seed(seed)
                    # print self.amp_mod[0].shape
                    gt_spikes.append(convolve_single_template(st, spike_bin,
                                                              self.templates[st, :, np.argmax(self.mixing[st])],
                                                              modulation=True,
                                                              amp_mod=self.amp_mod[st][:,
                                                                      np.argmax(self.mixing[st])]))
                elif self.spike_modulation == 'template-all' or self.spike_modulation == 'all':
                    seed = np.random.randint(10000)
                    np.random.seed(seed)
                    self.recordings += convolve_templates_spiketrains(st, spike_bin, self.templates[st],
                                                                      modulation=True,
                                                                      amp_mod=self.amp_mod[st])
                    np.random.seed(seed)
                    # print self.amp_mod[0].shape
                    gt_spikes.append(convolve_single_template(st, spike_bin,
                                                              self.templates[st, :, np.argmax(self.mixing[st])],
                                                              modulation=True,
                                                              amp_mod=self.amp_mod[st]))
        pool.close()
        self.gt_spikes = np.array(gt_spikes)

        print 'Elapsed time ', time.time() - t_start

        self.clean_recordings = copy(self.recordings)

        # print 'Adding noise'
        # if self.noise_level > 0:
        #     if noise_mode == 'uncorrelated':
        #         self.additive_noise = self.noise_level * np.random.randn(self.recordings.shape[0],
        #                                                                  self.recordings.shape[1])
        #         self.recordings += self.additive_noise
        #     elif noise_mode == 'correlated-dist':
        #         # TODO divide in chunks
        #         cov_dist = np.zeros((n_elec, n_elec))
        #         for i, el in enumerate(mea_pos):
        #             for j, p in enumerate(mea_pos):
        #                 if i != j:
        #                     cov_dist[i, j] = (0.5*np.min(mea_pitch))/np.linalg.norm(el - p)
        #                 else:
        #                     cov_dist[i, j] = 1
        #
        #         self.additive_noise = np.random.multivariate_normal(np.zeros(n_elec), cov_dist,
        #                                                        size=(self.recordings.shape[0], self.recordings.shape[1]))
        #         self.recordings += self.additive_noise
        # elif self.noise_level == 'experimental':
        #     print 'experimental noise model'
        # else:
        #     print 'Noise level is set to 0'
        print 'Adding noise'
        if self.noise_level > 0:
            if noise_mode == 'uncorrelated':
                if len(chunks_noise) > 0:
                    recording_chunks = []
                    for ch, chunk in enumerate(chunks_noise):
                        print 'Generating noise chunk ', ch + 1, ' of ', len(chunks_noise)
                        idxs = np.where((self.times >= chunk[0]) & (self.times < chunk[1]))[0]
                        additive_noise = self.noise_level * np.random.randn(self.recordings.shape[0],
                                                                            len(idxs))
                        self.recordings[:, idxs] += additive_noise
                else:
                    # self.additive_noise = self.noise_level * np.random.randn(self.recordings.shape[0],
                    #                                                          self.recordings.shape[1])
                    self.recordings += self.noise_level * np.random.randn(self.recordings.shape[0],
                                                                          self.recordings.shape[1])
            elif noise_mode == 'correlated-dist':
                # TODO divide in chunks
                cov_dist = np.zeros((n_elec, n_elec))
                for i, el in enumerate(mea_pos):
                    for j, p in enumerate(mea_pos):
                        if i != j:
                            cov_dist[i, j] = (0.5 * np.min(mea_pitch)) / np.linalg.norm(el - p)
                        else:
                            cov_dist[i, j] = 1

                self.additive_noise = np.random.multivariate_normal(np.zeros(n_elec), cov_dist,
                                                                    size=(
                                                                        self.recordings.shape[0],
                                                                        self.recordings.shape[1]))
                self.recordings += self.additive_noise
        elif self.noise_level == 'experimental':
            print 'experimental noise model'
        else:
            print 'Noise level is set to 0'

        if self.filter:
            print 'Filtering signals'
            self.bp = [300, 6000]*pq.Hz
            if self.fs/2. < self.bp[1]:
                self.recordings = filter_analog_signals(self.recordings, freq=self.bp[0], fs=self.fs,
                                                        filter_type='highpass')
            else:
                self.recordings = filter_analog_signals(self.recordings, freq=self.bp, fs=self.fs)
            if self.plot_figures:
                plot_mea_recording(self.recordings, self.mea_pos, self.mea_pitch, colors='g')

        if self.save:
            self.save_spikes()


    def save_spikes(self):
        ''' save meta data in old fashioned format'''
        self.rec_name = 'recording_' + self.rotation_type + '_' + self.electrode_name + '_' + str(self.n_cells) \
                        + '_' + str(self.duration).replace(' ','') + '_' + self.noise_mode + '_' \
                        + str(self.noise_level) + '_' + str(self.f_exc).replace(' ','') + '_' \
                        + str(self.f_inh).replace(' ','') + '_modulation_' + self.spike_modulation \
                        + '_' + time.strftime("%d-%m-%Y:%H:%M") + '_' + str(self.seed) + '_intermittent'

        rec_dir = join(root_folder, 'recordings', 'convolution', 'intermittent', self.electrode_name)
        self.rec_path = join(rec_dir, self.rec_name)
        os.makedirs(self.rec_path)
        # Save meta_info yaml
        with open(join(self.rec_path, 'rec_info.yaml'), 'w') as f:

            # ipdb.set_trace()
            general = {'spike_folder': self.spike_folder, 'rotation': self.rotation_type,
                       'pitch': self.mea_pitch, 'electrode name': str(self.electrode_name),
                       'MEA dimension': self.mea_dim, 'fs': str(self.fs), 'duration': str(self.duration),
                       'seed': self.seed}

            templates = {'pad_len': str(self.pad_len)}

            spikegen = {'duration': str(self.duration),
                        'f_exc': str(self.f_exc), 'f_inh': str(self.f_inh),
                        'p_exc': round(self.p_exc,4), 'p_inh': round(self.p_inh, 4), 'n_cells': self.n_cells,
                        'bound_x': self.bound_x, 'min_amp': self.min_amp, 'min_dist': self.min_dist}

            intermittent = {'n_int': str(self.n_int_cells), 'f_int': str(self.f_int),
                            't_int': str(self.t_int), 't_burst': str(self.t_burst),
                            'idx_int': str(self.spgen.idxs_int)}

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

            if self.filter:
                filter = {'filter': self.filter, 'bp': str(self.bp)}
            else:
                filter = {'filter': self.filter}


            # create dictionary for yaml file
            data_yaml = {'General': general,
                         'Templates': templates,
                         'Spikegen': spikegen,
                         'Intermittent': intermittent,
                         'Synchrony': synchrony,
                         'Modulation': modulation,
                         'Filter': filter,
                         'Noise': noise
                         }

            yaml.dump(data_yaml, f, default_flow_style=False)

        np.save(join(self.rec_path,'recordings'), self.recordings)
        np.save(join(self.rec_path,'spiketrains'), self.spgen.all_spiketrains)
        np.save(join(self.rec_path,'mixing'), self.mixing)
        np.save(join(self.rec_path,'sources'), self.gt_spikes)
        np.save(join(self.rec_path,'templates'), self.templates)
        np.save(join(self.rec_path,'templates_loc'), self.templates_loc)
        np.save(join(self.rec_path,'templates_cat'), self.templates_cat)
        np.save(join(self.rec_path,'templates_bincat'), self.templates_cat)

        print 'Saved ', self.rec_path


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
    if '-nint' in sys.argv:
        pos = sys.argv.index('-nint')
        nint = int(sys.argv[pos + 1])
    else:
        nint = 1
    if '-fint' in sys.argv:
        pos = sys.argv.index('-fint')
        fint = int(sys.argv[pos + 1])
    else:
        fint = 30
    if '-tint' in sys.argv:
        pos = sys.argv.index('-tint')
        tint = float(sys.argv[pos + 1])
    else:
        tint = 5
    if '-tburst' in sys.argv:
        pos = sys.argv.index('-tburst')
        tburst = float(sys.argv[pos + 1])
    else:
        tburst = 2
    if '-trem' in sys.argv:
        pos = sys.argv.index('-trem')
        trem = float(sys.argv[pos + 1])
    else:
        trem = 5
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
        sync = 0
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
        noiselev = 10.
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
    if '-nosave' in sys.argv:
        save=False
    else:
        save=True
    if '-tempmod' in sys.argv:
        modulation='template-all'
    if '-elmod' in sys.argv:
        modulation='electrode-all'
    if '-seed' in sys.argv:
        pos = sys.argv.index('-seed')
        seed = int(sys.argv[pos + 1])
    else:
        seed = np.random.randint(10000)

    if len(sys.argv) == 1:
        print 'Arguments: \n   -f filename\n   -dur duration\n   -freq sampling frequency (in kHz)\n' \
              '   -ncells number of cells\n   -nint number of intermittent cells\n   -fint freq of intermittent\n' \
              '   -tint time between intermittent bursts\n' \
              '   -pexc proportion of exc cells\n   -bx x boundaries [xmin,xmax]\n   -minamp minimum amplitude\n' \
              '   -noise uncorrelated-correlated\n   -noiselev level of rms noise in uV\n   -dur duration\n' \
              '   -fexc freq exc neurons\n   -finh freq inh neurons\n   -nofilter if filter or not\n' \
              '   -over overlapping spike threshold (0.6)\n   -sync added synchorny rate\n' \
              '   -nomod no spike amp modulation\n   -tempmod  modulate each template amplitude with gaussian\n' \
              '   -elmod  modulate each template amplitude with gaussian\n   -noplot\n'
    elif '-f' not in sys.argv:
        raise AttributeError('Provide model folder for data')
    else:
        gs = GenST(save=save, spike_folder=spike_folder, fs=freq, n_cells=ncells, n_int_cells=nint,
                   f_int=fint, t_int=tint, t_burst=tburst, p_exc=pexc, duration=dur,
                   bound_x=bx, min_amp=minamp, noise_mode=noise, noise_level=noiselev, f_exc=fexc, f_inh=finh,
                   filter=filter, over=over, sync=sync, modulation=modulation, min_dist=mindist,
                   plot_figures=plot_figures, seed=seed)





