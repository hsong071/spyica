#!/bin/bash

sorters='ica klusta kilosort mountainsort spykingcircus yass'

if [ $# == 0 ]; then
    echo "Supply recording path (list) or recording_folder, ncells, noise, dur, probe, seed, and spikesorters ('list') or recording_folder 'all' (run from Spyica root folder)"
else
    if [ $# == 2 ] && [ "$2" == "all" ]; then
        echo "Spikesorting all recordings"
        rec_folder=$1
        for rec in "$rec_folder"*
            do
            echo "Spikesorting $rec"
            for ss in $sorters
                do
                python spike_sorting.py -r $rec -mod $ss -noplot
                done
            done
    elif [ $# == 2 ]; then
        rec=$1
	sorters=$2
        echo "Spikesorting $rec"
        for ss in $sorters
            do
            python ../spike_sorting.py -r $rec -mod $ss -noplot
            done
    elif [ $# == 7 ]; then
        rec_folder=$1
        ncells=$2
        noise=$3
        dur=$4
        probe=$5
        seed=$6
	    sorters=$7

        if [ "$ncells" != "all" ] && [ "$noise" == "all" ]; then
            for rec in "$rec_folder"*
            do
                if [[ "$rec" == *"_"$ncells"_"* ]] && [[ "$rec" == *"_"$dur"s_"* ]] && [[ "$rec" == *"$seed"* ]] &&
                [[ "$rec" == *"$probe"* ]]; then
                    echo "Spikesorting $rec"
                    for ss in $sorters
                        do
                        python spike_sorting.py -r $rec -mod $ss -noplot
                        done
                fi
            done
        elif [[ "$ncells" == "all" ]] && [[ "$noise" != "all" ]]; then
            for rec in "$rec_folder"*
                do
                    if [[ "$rec" == *"uncorrelated_$noise"* ]] && [[ "$rec" == *"_"$dur"s_"* ]] &&
                    [[ "$rec" == *"$seed"* ]] && [[ "$rec" == *"$probe"* ]]; then
                        echo "Spikesorting $rec"
                        for ss in $sorters
                            do
                            python spike_sorting.py -r $rec -mod $ss -noplot
                            done
                    fi
                done
        elif [[ "$dur" == "all" ]] && [[ "$noise" != "all" ]]; then
            for rec in "$rec_folder"*
                do
                    if [[ "$rec" == *"uncorrelated_$noise"* ]] && [[ "$rec" == *"_"$ncells"_"* ]] &&
                    [[ "$rec" == *"$seed"* ]] && [[ "$rec" == *"$probe"* ]]; then
                        echo "Spikesorting $rec"
                        for ss in $sorters
                            do
                            python spike_sorting.py -r $rec -mod $ss -noplot
                            done
                    fi
                done
        else
            echo "_"$dur"s_"
            echo  "_"$ncells"_"
            echo "uncorrelated_"$noise""
            for rec in "$rec_folder"*
                do
                    if [[ "$rec" == *"uncorrelated_"$noise""* ]] && [[ "$rec" == *"_"$ncells"_"* ]] &&
                    [[ "$rec" == *"_"$dur"s_"* ]] && [[ "$rec" == *"$seed"* ]] &&
                    [[ "$rec" == *"$probe"* ]]; then
                        echo "Spikesorting $rec"
                        for ss in $sorters
                            do
                            python spike_sorting.py -r $rec -mod $ss -noplot
                            done
                    fi
                done
        fi
    fi
fi
echo All done

