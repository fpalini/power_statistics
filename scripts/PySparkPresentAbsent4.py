#! /usr/bin/python3
import re
import os
import os.path
import sys
import glob
import tempfile
import shutil
import copy
import subprocess
import math

import numpy as np
import py_kmc_api as kmc

from operator import add
import pyspark
from pyspark.sql import SparkSession
from pyspark import SparkFiles


hdfsPrefixPath = 'hdfs://master2:9000/user/cattaneo/data/dataset'
inputRE = '*.fasta'

# models = ['Uniform', 'MotifRepl-U', 'PatTransf-U', 'Uniform-T1']
models = ['ShuffledEColi', 'MotifRepl-Sh', 'PatTransf-Sh', 'ShuffledEColi-T1']
#lengths = range(1000, 50001, 1000) # small dataset
#gVals = [10, 50, 100]
nTests = 1000
minK = 4
maxK = 32
stepK = 4
sketchSizes = [1000, 10000, 100000]
outFilePrefix = 'PresentAbsentECData'


class EntropyData:
    def __init__(self, nKeys, totalKmerCnt, Hk):
        self.nKeys = nKeys
        self.totalKmerCnt = totalKmerCnt;
        self.Hk = Hk

    def getDelta(self):
        return float(self.nKeys) / (2 * self.totalKmerCnt)

    def getError(self):
        return self.getDelta() / self.Hk

class MashData:
    def __init__(self, cmdResults):
        mr = cmdResults.split()
        mashAN = mr[4].decode('UTF-8')
        self.Pv = float(mr[2])
        self.dist = float(mr[3])
        try:
            ns = mashAN.index('/')
            self.A = int(mashAN[:ns])
            self.N = int(mashAN[ns+1:])
        except ValueError:
            self.A = 0
            self.N = 0




# load histogram for both sequences (for counter based measures such as D2)
def loadHistogram(kmerDict, histFile, pairId):

    ndx = 0 if pairId == 'A' else 1
    kmcFile = kmc.KMCFile()
    if (kmcFile.OpenForListing(histFile)):
        print("file: %s Opened." % histFile)
    else:
        raise IOError( "OpenForListing failed for %s DB." % histFile)

    kmer = kmc.KmerAPI( kmcFile.KmerLength())
    cnt  = kmc.Count()

    totalKmerCnt = 0
    totalDistinct = 0
    # histFile contiene il DB con l'istogramma di una sola sequenza prodotto con kmc 3
    kmcFile.RestartListing()
    while(kmcFile.ReadNextKmer( kmer, cnt)):
        strKmer = kmer.__str__()
        count = cnt.value
        totalKmerCnt += count
        totalDistinct += 1
        if strKmer in kmerDict:

            cntTuple = kmerDict[strKmer]
            kmerDict[strKmer] = (cntTuple[0] + count, 0) if ndx == 0 else (cntTuple[0], cntTuple[1] + count)
        else:
            kmerDict[strKmer] = (count, 0) if ndx == 0 else (0, count) # # first time meet or kmer not present in sequence A

    if (kmcFile.KmerCount() != totalDistinct):
        raise ValueError( "Loaded %d distinct kmers vs %d" % (totalDistinct, kmcFile.KmerCount()))

    kmcFile.Close()
    Hk = sequenceEntropy( kmerDict, pairId, totalKmerCnt)

    return (totalDistinct, totalKmerCnt, Hk)




# calcola i valori dell'entropia per non caricare due volte l'istogramma
def sequenceEntropy( seqDict, pairID, totalKmerCnt):

    ndx = 0 if pairID == 'A' else 1
    totalProb = 0.0
    Hk = 0.0
    for key, cntTuple in seqDict.items():
        cnt = cntTuple[ndx]
        if (cnt > 0):
            prob = cnt / float(totalKmerCnt)
            totalProb = totalProb + prob
            Hk = Hk + prob * math.log(prob, 2)
            # print( "prob(%s) = %f log(prob) = %f" % (key, prob, math.log(prob, 2)))

    if (round(totalProb,0) != 1.0):
        raise ValueError("Somma(p) = %f must be 1.0. Aborting" % round(totalProb, 0))

    return Hk * -1





def extractStatistics(cnts):

    (left, right, both ) = (0,0,0)
    for i in range(cnts.shape[1]):
        if (cnts[0, i] == 0):
            if (cnts[1,i] > 0):
                right += 1  # presente solo a destra
            else:
                raise ValueError("double 0 in kmer histogram")
        else:
            if (cnts[1,i] == 0):
                left += 1   # solo a sinistra
            else:
                both += 1   # in entrambi

    return( both, left, right)






def extractKmers( inputDataset, k, tempDir, kmcOutputPrefix):

    # run kmc on the first sequence
    # -v - verbose mode (shows all parameter settings); default: false
    # -k<len> - k-mer length (k from 1 to 256; default: 25)
    # -m<size> - max amount of RAM in GB (from 1 to 1024); default: 12
    # -sm - use strict memory mode (memory limit from -m<n> switch will not be exceeded)
    # -p<par> - signature length (5, 6, 7, 8, 9, 10, 11); default: 9
    # -f<a/q/m/bam/kmc> - input in FASTA format (-fa), FASTQ format (-fq), multi FASTA (-fm) or BAM (-fbam) or KMC(-fkmc); default: FASTQ
    # -ci<value> - exclude k-mers occurring less than <value> times (default: 2)
    # -cs<value> - maximal value of a counter (default: 255)
    # -cx<value> - exclude k-mers occurring more of than <value> times (default: 1e9)
    # -b - turn off transformation of k-mers into canonical form
    # -r - turn on RAM-only mode
    # -n<value> - number of bins
    # -t<value> - total number of threads (default: no. of CPU cores)
    # -sf<value> - number of FASTQ reading threads
    # -sp<value> - number of splitting threads
    # -sr<value> - number of threads for 2nd stage
    # -hp - hide percentage progress (default: false)

    cmd = "/usr/local/bin/kmc -b -hp -k%d -m2 -fm -ci0 -cs1048575 -cx1000000 %s %s %s" % (k, inputDataset, kmcOutputPrefix, tempDir)
    p = subprocess.Popen(cmd.split())
    p.wait()
    print("cmd: %s returned: %s" % (cmd, p.returncode))

    # dump the result -> kmer histogram (no longer needed)
    # cmd = "/usr/local/bin/kmc_dump %s %s" % ( kmcOutputPrefix, histFile)
    # p = subprocess.Popen(cmd.split())
    # p.wait()
    # print("cmd: %s returned: %s" % (cmd, p.returncode))

    return




# run jaccard on sequence pair ds with kmer of length = k
def processLocalPair( ds, model, seqId, seqLen, gamma, k):

    # first extract kmer statistics for both sequences
    tempDir = os.path.dirname( ds)
    baseDS = os.path.basename( ds)

    inputDatasetA = '%s-A.fasta' % (ds)
    kmcOutputPrefixA = "%s/k=%d-%s-A" % (tempDir, k, baseDS)
    extractKmers(inputDatasetA, k, tempDir, kmcOutputPrefixA)

    inputDatasetB = '%s-B.fasta' % (ds)
    kmcOutputPrefixB = "%s/k=%d-%s-B" % (tempDir, k, baseDS)
    extractKmers(inputDatasetB, k, tempDir, kmcOutputPrefixB)

    data0 = [model, gamma, seqLen, seqId, k]

    # load kmers statistics from histogram files
    kmerDict = dict()
    (totalDistinctA, totalKmerCntA, HkA) = loadHistogram(kmerDict, kmcOutputPrefixA, 'A')
    entropySeqA = EntropyData( totalDistinctA, totalKmerCntA, HkA)

    (totalDistinctB, totalKmerCntB, HkB) = loadHistogram(kmerDict, kmcOutputPrefixB, 'B')
    entropySeqB = EntropyData( totalDistinctB, totalKmerCntB, HkB)

    i = 0
    cnts = np.empty( shape=(2, len( kmerDict.values())), dtype='int32')
    for v in kmerDict.values():
        cnts[0, i] = v[0]
        cnts[1, i] = v[1]
        i += 1

    kmerDict = None # free dictionary memory (=> counting are no longer necessary)

    dati3 = runCountBasedMeasures(cnts, k)

    (bothCnt, leftCnt, rightCnt) = extractStatistics(cnts)

    cnts = None # free ndarray with kmer counting

    # load kmers only from histogram files
    dati1 = runPresentAbsent(bothCnt, leftCnt, rightCnt, k)

    dati2 = runMash(inputDatasetA, inputDatasetB, k)

    dati4 = entropyData(entropySeqA, entropySeqB)

    os.remove(kmcOutputPrefixA+'.kmc_pre') # remove kmc output prefix file
    os.remove(kmcOutputPrefixA+'.kmc_suf') # remove kmc output suffix file

    os.remove(kmcOutputPrefixB+'.kmc_pre') # remove kmc output prefix file
    os.remove(kmcOutputPrefixB+'.kmc_suf') # remove kmc output suffix file

    return data0 + dati1 + dati2 + dati3 + dati4    # nuovo record output



def runCountBasedMeasures(cnts, k):
    D2totValue = 0
    EuclideanTotValue = 0
    for i in range(cnts.shape[1]):
        D2totValue = D2totValue + cnts[0,i] * cnts[1,i]
        d = cnts[0,i] - cnts[1,i]
        EuclideanTotValue = EuclideanTotValue + d * d

    NED = NormalizedSquaredEuclideanDistance( cnts)
    return [int(D2totValue), math.sqrt(EuclideanTotValue), float(NED)]




# we use numpy to not reimplment z-score stndardization from scratch
def NormalizedSquaredEuclideanDistance( vector):
    # (tot1, tot2) = (0, 0)
    # for x in vector:
    #     tot1 += x[0]
    #     tot2 += x[1]
    # n = len(vector)
    # mean1 = tot1 / n
    # mean2 = tot2 / n
    # (totDifferences1,totDifferences2) = (0,0)
    # for v in [((value[0] - mean1)**2, (value[1] - mean2)**2)  for value in vector]:
    #     totDifferences1 += v[0]
    #     totDifferences2 += v[1]
    # standardDeviation1 = (totDifferences1 / n) ** 0.5
    # standardDeviation2 = (totDifferences2 / n) ** 0.5
    # zscores = [((v[0] - mean1) / standardDeviation1, (v[1] - mean2) / standardDeviation2) for v in vector]

    # avg = np.mean( vector, axis=1)
    # std = np.std( vector, axis=1)
    #
    # z0_np = (vector[0] - avg[0]) / std[0]
    # z1_np = (vector[1] - avg[1]) / std[1]
    # tot = 0
    # for i in range(vector.shape[1]):
    #     tot += ((z0_np[i] - z1_np[i]) ** 2)
    #
    # ZEu = tot ** 0.5
    #
    # m = vector.shape[1]
    # D = 2 * m * (1 - (np.dot(vector[0], vector[1]) - m * avg[0] * avg[1]) / (m * std[0] * std[1]))
    var = np.var( vector, axis=1)

    NED = 0.5 * np.var(vector[0] - vector[1]) / (var[0] + var[1])
    return NED




# run jaccard on sequence pair ds with kmer of length = k
def runPresentAbsent(  bothCnt, leftCnt, rightCnt, k):

    print("left: %d, right: %d" % (leftCnt, rightCnt))
    A = int(bothCnt)
    B = int(leftCnt)
    C = int(rightCnt)

    NMax = pow(4, k)
    M01M10 = leftCnt + rightCnt
    M01M10M11 = bothCnt + M01M10
    absentCnt = NMax - (A + B + C) # NMax - M01M10M11
    D = absentCnt
    # (M10 + M01) / (M11 + M10 + M01)

    # Anderberg dissimilarity => Anderberg = 1 - (A/(A + B) + A/(A + C) + D/(C + D) + D/(B + D))/4
    try:
        anderberg = 1 - (A/float(A + B) + A/float(A + C) + D/float(C + D) + D/float(B + D))/4.0
    except (ZeroDivisionError, ValueError):
        anderberg = 1.000001

    # Antidice dissimilarity => Antidice = 1 - A/(A + 2(B + C))
    try:
        antidice = 1 - A / float(A + 2.0 * (B + C))
    except (ZeroDivisionError, ValueError):
        antidice = 1.000001

    # Dice dissimilarity => Dice = 1 - 2A/(2A + B + C)
    try:
        dice = 1 - 2*A / float(2.0*A + B + C)
    except (ZeroDivisionError, ValueError):
        dice  = 1.000001
    # Gower dissimilarity => Gower = 1 - A x D/sqrt(A + B) x(A + C) x (D + B x (D + C)
    try:
        gower = 1 - A * D / math.sqrt((A + B) * (A + C) * (D + B * (D + C)))
    except (ZeroDivisionError, ValueError):
        gower = 1.000001

    # Hamman dissimilarity => Hamman = 1 - [((A + D) - (B + C))/N]2
    try:
        hamman = 1 - math.pow((((A + D) - (B + C)) / float(NMax)), 2.0)
    except (ZeroDivisionError, ValueError):
        hamman = 1.000001

    # Hamming dissimilarity => Hamming = (B + C)/N
    try:
        hamming = (B + C)/ NMax
    except (ZeroDivisionError, ValueError):
        hamming = 1.000001

    # Jaccard dissimilarity => Jaccard = 1 - A/(N - D)
    try:
        jaccard = 1 - A / (NMax - D)
    except (ZeroDivisionError, ValueError):
        jaccard = 1.000001

    jaccardDistance = 1 - min( 1.0, A / float(NMax - D))

    # Kulczynski dissimilarity => Kulczynski = 1 - (A/(A + B) + A/(A + C)) / 2
    try:
        kulczynski = 1 - (A / float(A + B) + A / float(A + C)) / 2.0
    except (ZeroDivisionError, ValueError):
        kulczynski = 1.000001

    # Matching dissimilarity => Matching = 1 - (A + D)/N
    try:
        matching = 1 - (A + D) / NMax
    except (ZeroDivisionError,ValueError):
        matching = 1.000001

    # Ochiai dissimilarity => Ochiai = 1 - A/sqrt(A + B) x (A + C)
    try:
        ochiai = 1 - A / math.sqrt((A + B) * (A + C))
    except (ZeroDivisionError, ValueError):
        ochiai = 1.000001

    # Phi dissimilarity => Phi = 1 - [(A x  B x  C x D)/sqrt(A + B) x (A + C) x (D + B) x (D + C)]2
    try:
        phi = 1 - math.pow((A * B * C * D)/ math.sqrt((A + B) * (A + C) * (D + B) * (D + C)), 2.0)
    except (ZeroDivisionError, ValueError):
        phi = 1.000001

    # Russel dissimilarity => Russel = 1 - A/N
    try:
        russel = 1 - A / NMax
    except (ZeroDivisionError, ValueError):
        russel = 1.000001

    # Sneath dissimilarity => Sneath = 1 - 2(A + D)/(2 x (A + D) + (B + C))
    try:
        sneath = 1 - 2.0 * (A + D) / (2.0 * (A + D) + (B + C))
    except (ZeroDivisionError, ValueError):
        sneath = 1.000001

    # Tanimoto dissimilarity => Tanimoto = 1 - (A + D)/((A + D) + 2(B + C))
    try:
        tanimoto = 1 - (A + D) / float((A + D) + 2.0 * (B + C))
    except (ZeroDivisionError, ValueError):
        tanimoto = 1.000001

    # Yule dissimilarity => Yule = 1 - [(A x D - B x C)/(A x D + B x C)]2
    try:
        yule = 1 - math.pow(((A * D - B * C) / float(A * D + B * C)), 2.0)
    except (ZeroDivisionError, ValueError):
        yule = 1.000001

    # salva il risultato nel file CSV
    # dati present / absent e distanze present absent
    data1 = [ A, B, C, str(D), str(NMax),
             anderberg, antidice, dice, gower, hamman, hamming, jaccard,
             kulczynski, matching, ochiai, phi, russel, sneath, tanimoto, yule]

    return data1




def runMash(inputDS1, inputDS2, k):
    # run mash on the same sequence pair
    mashValues = []
    for i in range(len(sketchSizes)):
        # extract mash sketch from the first sequence
        cmd = "/usr/local/bin/mash sketch -s %d -k %d %s" % (sketchSizes[i], k, inputDS1)
        p = subprocess.Popen(cmd.split())
        p.wait()

        cmd = "/usr/local/bin/mash sketch -s %d -k %d %s" % (sketchSizes[i], k, inputDS2)
        p = subprocess.Popen(cmd.split())
        p.wait()

        cmd = "/usr/local/bin/mash dist %s.msh %s.msh" % (inputDS1, inputDS2)
        out = subprocess.check_output(cmd.split())

        mashValues.append( MashData( out))

    # dati mash distance
    data2 = []
    for i in range(len(sketchSizes)):
        data2.append( mashValues[i].Pv)
        data2.append( mashValues[i].dist)
        data2.append( mashValues[i].A)
        data2.append( mashValues[i].N)

    # clean up remove kmc temporary files
    os.remove(inputDS1 + '.msh')
    os.remove(inputDS2 + '.msh')

    return data2





def entropyData(entropySeqA, entropySeqB):
    # dati errore entropia e rappresentazione present/absent
    return  [entropySeqA.nKeys, 2 * entropySeqA.totalKmerCnt, entropySeqA.getDelta(), entropySeqA.Hk, entropySeqA.getError(),
             entropySeqB.nKeys, 2 * entropySeqB.totalKmerCnt, entropySeqB.getDelta(), entropySeqB.Hk, entropySeqB.getError()]





def saveSingleSequence(prefix, seq, header, sequence):
    # save sequence
    fileName = "%s-%s.fasta" % (prefix, seq)
    with open(fileName, "w") as outText:
        outText.write(header)
        outText.write('\n')
        outText.write(sequence)
        outText.write('\n')





# processo una coppia del tipo (id, (hdrA, seqA), (hdrB, seqB))
def processPairs(seqPair):

    dataset = seqPair[0]
    m = re.search(r'^(.*)-(\d+)\.(\d+)(.*)', dataset)
    if (m is None):
        raise ValueError("Malformed sequences pair (name=<%s>)" % dataset)
    else:
        model = m.group(1)
        nPairs = int(m.group(2))
        seqLen = int(m.group(3))
        gamma = m.group(4)

    header = seqPair[1][0]
    m = re.search(r'^>(.+)\.(\d+)(.*)-([AB]$)', header)
    if (m is not None):
        # seqName = m.group(1)
        seqId = int(m.group(2))
        # gValue = m.group(3)
        pairId = m.group(4)

    # process local file system temporary directory
    tempDir = tempfile.mkdtemp()

    # common prefix
    fileNamePrefix = "%s/%s-%04d.%d%s" % (tempDir, model, seqId, seqLen, gamma)
    # save sequence seqId-A
    saveSingleSequence(fileNamePrefix, 'A', seqPair[1][0], seqPair[1][1])
    # save sequence seqId-B
    saveSingleSequence(fileNamePrefix, 'B', seqPair[2][0], seqPair[2][1])

    results = []
    for k in range( minK, maxK+1, stepK):
        # run kmc on both the sequences and eval A, B, C, D + Mash + Entropy
        g = float(gamma[3:]) if (len(gamma) > 0) else 0.0
        results.append(processLocalPair(fileNamePrefix, model, seqId, seqLen, g, k))

    # clean up
    # do not remove dataset on hdfs
    # remove histogram files (A & B) + mash sketch file and kmc temporary files
    try:
        print("Cleaning temporary directory %s" % (tempDir))
        shutil.rmtree(tempDir)
    except OSError as e:
        print("Error removing: %s: %s" % (tempDir, e.strerror))

    return results



# produce a list of sequence pairs with len nSeq
def splitPairs(ds):

    m = re.search(r'^(.*)-(\d+)\.(\d+)(.*).fasta', os.path.basename(ds[0]))
    if (m is None):
        raise ValueError("Malformed file name <%s>" % ds[0])
    else:
        model = m.group(1)
        nPair = int(m.group(2))
        seqLen = int(m.group(3))
        gamma = m.group(4)

    # print("Splitting dataset: %s@%s" % (ds[0], os.uname()[1]))

    lines = ds[1].split()
    if (len(lines) != 4):
        raise ValueError("missing sequence data (len = %d)" % len(lines))

    ids = ['A', 'B']
    seqPair = [ 'name', [ 'header', 'sequenceA'], ['headerB', 'sequenceB']]
    seqLabel = "%s-%d.%d%s" % (model, nPair, seqLen, gamma) #uguale per tutto il dataset
    seqPair[0] = seqLabel
    for seq in range(2):
        header = lines[2*seq]
        m = re.search(r'^>(.+)\.(\d+)(.*)-([AB]$)', header)
        if (m is not None):
            # seqName = m.group(1) e gValue = m.group(3) sono unici per l'intero file
            seqId = int(m.group(2))
            pairId =  m.group(4)
        else:
            raise ValueError("Malformed sequence header: %s" % header)

        seqPair[seq+1][0] = header
        if (pairId != ids[seq]):
            raise ValueError("sequence out of order %s vs %s" % (pairId, ids[seq]))

        sequence = lines[2*seq + 1]
        seqPair[seq+1][1] = sequence

    return seqPair






def main():
    global hdfsDataDir, hdfsPrefixPath,  outFilePrefix
    
    argNum = len(sys.argv)
    if (argNum < 2 or argNum > 3):
        """
            Usage: PySparkPresentAbsent4 seqLength [dataMode]
        """
    else:
        seqLen = int(sys.argv[1])
        dataMode = sys.argv[2] if (argNum > 2) else ""
        hdfsDataDir = '%s-%s-1000/len=%d' % (hdfsPrefixPath, dataMode, seqLen)
        outFile = '%s-%s-1000/%s-%s.%d.csv' % (hdfsPrefixPath, dataMode, outFilePrefix, dataMode, seqLen)

    spark = SparkSession \
        .builder \
        .appName("%s %d" % (os.path.basename( sys.argv[0]), seqLen)) \
        .getOrCreate()

    sc = spark.sparkContext

    sc2 = spark._jsc.sc()
    nWorkers =  len([executor.host() for executor in sc2.statusTracker().getExecutorInfos()]) -1
    inputDataset = '%s/%s' % (hdfsDataDir, inputRE)

    print("%d workers, hdfsDataDir: %s, dataMode: %s" % (nWorkers, hdfsDataDir, dataMode))

    rdd = sc.wholeTextFiles( inputDataset, minPartitions = 48*100) # 100 tasks per 48 executors
    # print("Number of Partitions: " + str(rdd.getNumPartitions()))
    #
    # .map(lambda x: (x[0], x[1][0], x[1][1], x[2][0], x[2][1]))
    # columns = ['name', 'seqA', 'contentA', 'seqB', 'contentB']

    print("**** RDD number of Partitions: %d" % rdd.getNumPartitions())

    pairs = rdd.map(lambda x: splitPairs(x))
    print("**** pairs number of Partitions: %d" % pairs.getNumPartitions())

    counts = pairs.flatMap(lambda x: processPairs(x))
    print("**** counts number of Partitions: %d" % counts.getNumPartitions())

    columns0 = ['model', 'gamma', 'seqLen', 'pairId', 'k'] # dati 0
    columns1 = [ 'A', 'B', 'C', 'D', 'N',
               'Anderberg', 'Antidice', 'Dice', 'Gower', 'Hamman', 'Hamming',
                'Jaccard', 'Kulczynski', 'Matching', 'Ochiai',
                'Phi', 'Russel', 'Sneath', 'Tanimoto', 'Yule']

    columns2 = []
    for ss in sketchSizes:
        columns2.append( 'Mash Pv (%d)' % ss)
        columns2.append( 'Mash Distance(%d)' % ss)
        columns2.append( 'A (%d)' % ss)
        columns2.append( 'N (%d)' % ss)

    columns3 = [ 'D2', 'Euclidean', 'Euclid_norm']

    columns4 = ['NKeysA', '2*totalCntA', 'deltaA', 'HkA', 'errorA',
                'NKeysB', '2*totalCntB', 'deltaB', 'HkB', 'errorB']

    df = counts.toDF(columns0 + columns1 + columns2 + columns3 + columns4)

    # df.write.format("csv").save(outFile)
    df.write.option("header",True).csv(outFile)
    spark.stop()






if __name__ == "__main__":
    main()
