#! /bin/bash

scDir='/Users/pipp8/Universita/Src/IdeaProjects/PowerStatistics/scripts'
scDir='/home/cattaneo/spark/PowerStatistics/scripts'

hist=hist
temp=tmp

if [ "$#" -lt 1 ]; then
    files=*.fasta
else
    files=$@
fi

minK=4
maxK=26

mkdir $temp
mkdir $hist

for f in $files; do
    k=$minK
    while ((k <= maxK)); do
      echo "dataset: $f k = $k"
      kmc -k$k -m2 -fm -ci0 -cs1000000 -n77 $f tt $temp
      base=$(basename $f .fasta)
      outFile=$hist/distk=${k}_${base}.hist
      kmc_dump tt $outFile

      $scDir/hist2delta-kmc.py $outFile
      ((k+=2))
    done
done

