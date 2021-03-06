'''
Generate recordings with unit drifiting in a similar direction at a certain velocity
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

class GenST:
    def __init__(self, save=False, spike_folder=None, fs=None, noise_mode=None, n_cells=None, p_exc=None,
                 bound_x=[], min_amp=None, noise_level=None, duration=None, f_exc=None, f_inh=None,
                 filter=True, over=None, sync=None, modulation=True, min_dist=None, plot_figures=True,
                 seed=2904, preferred_dir=90, drift_velocity=5, t_start_drift=5):
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
        self.chunk_duration=0*pq.s

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
        self.t_start_drift = int(t_start_drift) * pq.s

        print 'Modulation: ', self.spike_modulation

        parallel=False

        print 'Loading spikes and MEA'
        if 'drifting' in self.spike_folder:
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

            drift_dir = np.array([(p[-1] - p[0])/np.linalg.norm(p[-1] - p[0]) for p in loc])
            drift_dir_ang = np.array([np.sign(p[2])*np.rad2deg(np.arccos(np.dot(p[1:],[1,0]))) for p in drift_dir])
            drift_dist = np.array([np.linalg.norm(p[-1] - p[0]) for p in loc])
            init_loc = loc[:, 0]
        else:
            raise Exception('Provide drifting spikes!')

        all_categories = ['BP', 'BTC', 'ChC', 'DBC', 'LBC', 'MC', 'NBC',
                          'NGC', 'SBC', 'STPC', 'TTPC1', 'TTPC2', 'UTPC']

        exc_categories = ['STPC', 'TTPC1', 'TTPC2', 'UTPC']
        inh_categories = ['BP', 'BTC', 'ChC', 'DBC', 'LBC', 'MC', 'NBC', 'NGC', 'SBC']
        bin_cat = get_binary_cat(cat, exc_categories, inh_categories)

        # load MEA info
        # with open(join(root_folder, 'electrodes', self.electrode_name + '.json')) as meafile:
        #     elinfo = json.load(meafile)
        elinfo = MEA.return_mea_info(self.electrode_name)

        x_plane = 0.
        pos = MEA.get_elcoords(x_plane, **elinfo)

        x_plane = 0.
        self.mea_pos = MEA.get_elcoords(x_plane, **elinfo)
        self.mea_pitch = elinfo['pitch']
        self.mea_dim = elinfo['dim']
        mea_shape=elinfo['shape']

        n_elec = spikes.shape[2]
        # this is fixed from recordings
        spike_duration = 7*pq.ms
        spike_fs = 32*pq.kHz

        print 'Selecting cells'
        n_exc = int(self.p_exc*self.n_cells)
        n_inh = self.n_cells - n_exc
        print n_exc, ' excitatory and ', n_inh, ' inhibitory'

        idxs_cells = select_cells(init_loc, spikes, bin_cat, n_exc, n_inh, bound_x=self.bound_x, min_amp=self.min_amp,
                                  min_dist=self.min_dist, drift=True, drift_dir_ang=drift_dir_ang,
                                  preferred_dir=preferred_dir, ang_tol=15, verbose=True)
        drift_velocity_ums = drift_velocity / 60.
        vel_vector = drift_velocity_ums * np.array([np.cos(np.deg2rad(preferred_dir)),
                                                    np.sin(np.deg2rad(preferred_dir))])

        print vel_vector

        self.templates_cat = cat[idxs_cells]
        self.templates_loc = loc[idxs_cells]
        self.templates_bin = bin_cat[idxs_cells]
        self.templates_dir = drift_dir_ang[idxs_cells]
        self.templates_dist = drift_dist[idxs_cells]
        templates = spikes[idxs_cells]

        # mixing matrices
        mixing = []
        for tem in templates:
            dt = 2 ** -5
            feat = get_EAP_features(tem[0], ['Na'], dt=dt)
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
        if templates.shape[3] != n_resample:
            templates_pol = np.zeros((templates.shape[0], templates.shape[1], templates.shape[2], n_resample))
            print 'Resampling spikes'
            for t, tem in enumerate(templates):
                tem_pad = np.pad(tem, [(0,0), (0,0), pad_samples], 'edge')
                tem_poly = ss.resample_poly(tem_pad, up, down, axis=2)
                templates_pol[t] = tem_poly[:, :, int(sampling_ratio*pad_samples[0]):int(sampling_ratio*pad_samples[0])                                                                       +n_resample]
            self.resample=True
        else:
            templates_pol = templates

        templates_pad = []
        templates_spl = []
        for t, tem in enumerate(templates_pol):
            print 'Padding edges: neuron ', t+1, ' of ', len(templates_pol)
            templates_pad_p = []
            for tem_p in tem:
                tem_p, spl = cubic_padding(tem_p, self.pad_len, self.fs)
                templates_pad_p.append(tem_p)
                # templates_spl.append(spl)
            templates_pad.append(templates_pad_p)

        self.templates_pad = np.array(templates_pad)

        # sampling jitter
        n_jitters = 5
        upsample = 8
        jitter = 1. / self.fs
        templates_jitter = []
        for t, temp in enumerate(templates_pad):
            print 'Jittering: neuron ', t+1, ' of ', len(templates_pol)
            templates_jitter_p = []
            for tem_p in temp:
                temp_up = ss.resample_poly(tem_p, upsample, 1., axis=1)
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

                templates_jitter_p.append(temp_jitt)
            templates_jitter.append(templates_jitter_p)

        templates_jitter = np.array(templates_jitter)

        self.templates = np.array(templates_jitter)
        del templates_pol, templates_jitter_p, templates_jitter, templates_pad, templates_pad_p


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

        if self.sync_rate != 0:
            print 'Adding synchrony on overlapping spikes'
            self.overlapping = find_overlapping_spikes(self.templates, thresh=self.overlap_threshold)

            for over in self.overlapping:
                self.spgen.add_synchrony(over, rate=self.sync_rate)

            if self.plot_figures and self.sync_rate != 0:
                ax = self.spgen.raster_plots()
                ax.set_title('After synchrony')
        else:
            self.overlapping = []


        # find SNR and annotate
        print 'Computing SNR'
        for t_i, temp in enumerate(self.templates):
            min_peak = np.min(temp)
            snr = np.abs(min_peak/float(noiselev))
            self.spgen.all_spiketrains[t_i].annotate(snr=snr)
            print min_peak, snr


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
        elif self.spike_modulation == 'template-mod':
            print 'Noisy modulation'
            for st in self.spgen.all_spiketrains:
                amp, cons = ISI_amplitude_modulation(st, mrand=self.mrand, sdrand=self.sdrand,
                                                     n_spikes=0, exp=self.exp, mem_ISI=self.mem_isi)
                self.amp_mod.append(amp)
                self.cons_spikes.append(cons)
        elif self.spike_modulation == 'electrode-mod':
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
        final_loc = []
        n_step_sec = 1
        dur = (n_samples / self.fs).rescale('s').magnitude
        t_steps = np.arange(0, dur, n_step_sec)
        mixing = np.zeros((len(templates), len(t_steps), n_elec))

        # divide in chunks
        chunks = []
        if self.duration > self.chunk_duration and self.chunk_duration != 0:
            start=0*pq.s
            finished=False
            while not finished:
                chunks.append([start, start+self.chunk_duration])
                start=start+self.chunk_duration
                if start >= self.duration:
                    finished = True
            print 'Chunks: ', chunks

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
                #TODO fix other modulation types
                print 'Convolving with spike ', st, ' out of ', spike_matrix.shape[0]
                if self.spike_modulation == 'none':
                    # reset random seed to keep sampling of jitter spike same
                    seed = np.random.randint(10000)
                    np.random.seed(seed)

                    rec, fin_pos, mix = convolve_drifting_templates_spiketrains(st, spike_bin, self.templates[st],
                                                                               fs=self.fs, loc=self.templates_loc[st],
                                                                               v_drift=vel_vector,
                                                                               t_start_drift=self.t_start_drift)
                    mixing[st] = mix

                    self.recordings += rec
                    final_loc.append(fin_pos)

                    np.random.seed(seed)
                    gt_spikes.append(convolve_single_template(st, spike_bin,
                                                              self.templates[st, 0, :, np.argmax(self.mixing[st])]))
                elif self.spike_modulation == 'electrode-mod':
                    seed = np.random.randint(10000)
                    np.random.seed(seed)

                    rec, fin_pos, mix = convolve_drifting_templates_spiketrains(st, spike_bin, self.templates[st],
                                                                           modulation=True, amp_mod=self.amp_mod[st],
                                                                           fs=self.fs, loc=self.templates_loc[st],
                                                                           v_drift=vel_vector,
                                                                           t_start_drift=self.t_start_drift)
                    # rec, fin_pos = convolve_templates_spiketrains(st, spike_bin, self.templates[st],
                    #                                                   modulation=True,
                    #
                    #                                       amp_mod=self.amp_mod[st])
                    mixing[st] = mix

                    self.recordings += rec
                    final_loc.append(fin_pos)

                    np.random.seed(seed)
                    # print self.amp_mod[0].shape
                    gt_spikes.append(convolve_single_template(st, spike_bin,
                                                              self.templates[st, 0, :, np.argmax(self.mixing[st])],
                                                              modulation=True,
                                                              amp_mod=self.amp_mod[st][:,
                                                                      np.argmax(self.mixing[st])]))
                elif self.spike_modulation == 'template-mod' or self.spike_modulation == 'all':
                    seed = np.random.randint(10000)
                    np.random.seed(seed)

                    rec, fin_pos, mix = convolve_drifting_templates_spiketrains(st, spike_bin, self.templates[st],
                                                                           modulation=True, amp_mod=self.amp_mod[st],
                                                                           fs=self.fs, loc=self.templates_loc[st],
                                                                           v_drift=vel_vector,
                                                                           t_start_drift=self.t_start_drift)
                    # rec, fin_pos = convolve_templates_spiketrains(st, spike_bin, self.templates[st],
                    #                                                   modulation=True,
                    #                                                   amp_mod=self.amp_mod[st])
                    mixing[st] = mix

                    self.recordings += rec
                    final_loc.append(fin_pos)

                    np.random.seed(seed)
                    # print self.amp_mod[0].shape
                    gt_spikes.append(convolve_single_template(st, spike_bin,
                                                              self.templates[st, 0, :, np.argmax(self.mixing[st])],
                                                              modulation=True,
                                                              amp_mod=self.amp_mod[st]))

        self.mixing_ev = mixing

        pool.close()
        self.gt_spikes = np.array(gt_spikes)
        self.templates_loc_final = np.array(final_loc)
        self.templates_loc_init = self.templates_loc[:, 0]

        if self.plot_figures:
            fig, ax_pos = plt.subplots()
            ax_pos.quiver(self.templates_loc_init[:, 1], self.templates_loc_init[:, 2],
                          self.templates_loc_final[:, 1] - self.templates_loc_init[:, 1],
                          self.templates_loc_final[:, 2] - self.templates_loc_init[:, 2])

            xmin = np.min([self.templates_loc_init[:, 1], self.templates_loc_final[:, 1]])
            xmax = np.max([self.templates_loc_init[:, 1], self.templates_loc_final[:, 1]])
            ymin = np.min([self.templates_loc_init[:, 2], self.templates_loc_final[:, 2]])
            ymax = np.max([self.templates_loc_init[:, 2], self.templates_loc_final[:, 2]])

            ax_pos.set_xlim([xmin-20, xmax+20])
            ax_pos.set_ylim([ymin-20, ymax+20])

        print 'Initial locations: ', self.templates_loc_init
        print 'Final locations: ', self.templates_loc_final

        # mixing matrices
        mixing_final = []
        templates_final = []
        for i, tem in enumerate(self.templates_pad):
            dt = 2 ** -5
            temp_idx = np.where([np.array_equal(p, self.templates_loc_final[i]) for p in self.templates_loc[i]])
            print temp_idx
            # raise Exception()
            feat = get_EAP_features(np.squeeze(tem[temp_idx]), ['Na'], dt=dt)
            mixing_final.append(-np.squeeze(feat['na']))
            templates_final.append(tem[temp_idx])
        self.mixing_final = np.array(mixing_final)
        self.templates_init = self.templates_pad[:, 0]
        self.templates_final = np.squeeze(np.array(templates_final))

        print 'Elapsed time ', time.time() - t_start

        self.clean_recordings = copy(self.recordings)

        print 'Adding noise'
        if self.noise_level > 0:
            if noise_mode == 'uncorrelated':
                self.additive_noise = self.noise_level * np.random.randn(self.recordings.shape[0],
                                                                         self.recordings.shape[1])
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
                        + '_' + time.strftime("%d-%m-%Y:%H:%M") + '_' + str(self.seed)

        rec_dir = join(root_folder, 'recordings', 'convolution', 'drifting', self.electrode_name)
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
                        'p_exc': round(self.p_exc, 4), 'p_inh': round(self.p_inh, 4), 'n_cells': self.n_cells,
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

            if self.filter:
                filter = {'filter': self.filter, 'bp': str(self.bp)}
            else:
                filter = {'filter': self.filter}


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
        np.save(join(self.rec_path,'templates'), self.templates_init)
        np.save(join(self.rec_path,'templates_final'), self.templates_final)
        np.save(join(self.rec_path,'sources'), self.gt_spikes)
        np.save(join(self.rec_path,'mixing'), self.mixing)
        np.save(join(self.rec_path,'mixing_ev'), self.mixing_ev)
        np.save(join(self.rec_path,'mixing_final'), self.mixing_final)
        np.save(join(self.rec_path,'templates_loc'), self.templates_loc_init)
        np.save(join(self.rec_path,'templates_loc_final'), self.templates_loc_final)
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
    if '-driftvel' in sys.argv:
        pos = sys.argv.index('-driftvel')
        driftvel = int(sys.argv[pos + 1])
    else:
        driftvel = 30
    if '-driftstart' in sys.argv:
        pos = sys.argv.index('-driftstart')
        startdrift = int(sys.argv[pos + 1])
    else:
        startdrift = 5
    if '-driftdir' in sys.argv:
        pos = sys.argv.index('-driftdir')
        driftdir = int(sys.argv[pos + 1])
    else:
        driftdir = 90
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
        bx = [10,60]
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
    if '-nosave' in sys.argv:
        save=False
    else:
        save=True
    if '-noplot' in sys.argv:
        plot_figures=False
    else:
        plot_figures=True
    if '-tempmod' in sys.argv:
        modulation='template-mod'
    if '-elmod' in sys.argv:
        modulation='electrode-mod'
    if '-seed' in sys.argv:
        pos = sys.argv.index('-seed')
        seed = int(sys.argv[pos + 1])
    else:
        seed = np.random.randint(10000)

    print modulation

    if len(sys.argv) == 1:
        print 'Arguments: \n   -f filename\n   -dur duration\n   -freq sampling frequency (in kHz)\n' \
              '   -ncells number of cells\n   -driftvel drift velocity in um/min\n   -driftstart drifting start (s)\n' \
              '   -driftdir preferred drift direction (deg)\n' \
              '   -pexc proportion of exc cells\n   -bx x boundaries [xmin,xmax]\n   -minamp minimum amplitude\n' \
              '   -noise uncorrelated-correlated\n   -noiselev level of rms noise in uV\n   -dur duration\n' \
              '   -fexc freq exc neurons\n   -finh freq inh neurons\n   -nofilter if filter or not\n' \
              '   -over overlapping spike threshold (0.6)\n   -sync added synchorny rate\n' \
              '   -nomod no spike amp modulation\n   -tempmod  modulate each template amplitude with gaussian\n' \
              '   -elmod  modulate each template amplitude with gaussian\n-noplot\n'
    elif '-f' not in sys.argv:
        raise AttributeError('Provide model folder for data')
    else:
        gs = GenST(save=save, spike_folder=spike_folder, fs=freq, n_cells=ncells, preferred_dir=driftdir,
                   drift_velocity=driftvel, t_start_drift=startdrift, p_exc=pexc, duration=dur,
                   bound_x=bx, min_amp=minamp, noise_mode=noise, noise_level=noiselev, f_exc=fexc, f_inh=finh,
                   filter=filter, over=over, sync=sync, modulation=modulation, min_dist=mindist,
                   plot_figures=plot_figures, seed=seed)





