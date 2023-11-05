//
// $Id: DatasetBuilder.scala 1716 2020-08-07 15:25:19Z cattaneo@dia.unisa.it $
//


package it.unisa.di.bio

import java.io.{BufferedWriter, File, FileNotFoundException, IOException, OutputStreamWriter}
import java.net.URI
import java.time.LocalDateTime
import java.util.Properties
import java.time.format.DateTimeFormatter

import org.apache.spark.{SparkConf, SparkContext}
import it.unisa.di.bio.Misc._
import org.apache.hadoop.conf.Configuration
import org.apache.hadoop.fs.{FileSystem, Path}
import org.apache.spark.rdd.RDD

import scala.collection.mutable.{ArrayBuffer, ListBuffer}
import scala.io.BufferedSource
import scala.math._
import scala.util.control.Breaks._

import com.concurrentthought.cla._


object KmerCompare {

  var local = true
  val debug = false

  var sc:  SparkContext = null
  val fileExt: String = "fasta"
  var savePath: String = "data/huge"
  var appProperties: Properties = null
  var parsed: Args = null

  var prefixPath : String = ""
  var inputFile1 : String = null
  var inputFile2 : String = null

  var hadoopConf: org.apache.hadoop.conf.Configuration = null




  def main(args: Array[String]) {

    val initialArgs: Args = """
                              |target/powerstatistics-1.0-SNAPSHOT.jar -m yarn|local kmerFile1 kmerFile2
                              | -m  | --mode      string  Spark cluster mode local|yarn
                              | [-p | --path      string] hdfs common prefix path
                              |                   inputfiles (two)
                              |""".stripMargin.toArgs

    parsed = initialArgs.process(args)

    // If here, successfully parsed the args and none where "--help" or "-h".
    parsed.printAllValues()

    if (parsed.remaining.length != 2) {
      println(s"Two paths of the input files to be compared mast be provided")
      sc.stop()
    }
    prefixPath = parsed.getOrElse("path", "")
    inputFile1 = prefixPath + "/" + parsed.remaining(0)
    inputFile2 = prefixPath + "/" + parsed.remaining(1)
    savePath = parsed.getOrElse( "output", "out")

    local = parsed.getOrElse("mode", "").compareTo("local") == 0

    val sparkConf = new SparkConf().setAppName( "kmercompare").setMaster(if (local) "local" else "yarn")
    sc = new SparkContext(sparkConf)
    hadoopConf = sc.hadoopConfiguration

    println(s"***App ${this.getClass.getCanonicalName} Started***")

    val seq1 = sc.textFile(inputFile1)
    val seq2 = sc.textFile(inputFile2)

    val is = seq1.intersection(seq2)

    val cnt = is.count()

    println(s"${cnt} common kmers")

    is.foreach( x => println(x.split("\t")(0)))
  }

}

