#!/bin/bash

if [ $# == 0 ];
  then
    echo "Supply recording path"
else
    if [ $# == 1 ] && [ "$1" == "all" ];
      then
        echo "Spikesorting all recordings"
    else
        rec=$1
        # Basic for loop
        sorters='ica klusta kilosort spykingcircus yass'
        for ss in $sorters
        do
        echo $ss
        python ../spike_sorting.py -r $rec -mod $ss -noplot
        done
        echo All done
    fi
fi