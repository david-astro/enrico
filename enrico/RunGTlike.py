#!/usr/bin/env python
import os,glob,os.path,math
from enrico import utils
from enrico.gtfunction import Observation
from enrico.fitmaker import FitMaker
from enrico.plotting import plot_sed_fromconfig
import Loggin
import SummedLikelihood
from enrico.xml_model import XmlMaker
from enrico.extern.configobj import ConfigObj
from utils import hasKey, isKey, typeirfs

def Analysis(folder, config, configgeneric=None, tag="", convtyp='-1', verbose = 1):

    mes = Loggin.Message()
    """ run an analysis"""
    Obs = Observation(folder, config, tag=tag)
    if verbose:
        utils._log('SUMMARY: ' + tag)
        Obs.printSum()

    FitRunner = FitMaker(Obs, config)##Class
    if config['Spectrum']['FitsGeneration'] == 'yes':
        FitRunner.FirstSelection(configgeneric) #Generates fits files for the coarse selection
        FitRunner.GenerateFits() #Generates fits files for the rest of the products
    return FitRunner

def GenAnalysisObjects(config, verbose = 1, xmlfile =""):

    mes = Loggin.Message()
    #check is the summed likelihood method should be used and get the
    #Analysis objects (observation and (Un)BinnedAnalysis objects)
    folder = config['out']

    # If there are no xml files, create it and print a warning
    if len(glob.glob(config['file']['xml'].replace('.xml','*.xml')))==0:
        mes.warning("Xml not found, creating one for the given config %s" %config['file']['xml'])
        XmlMaker(config)

    Fit = SummedLikelihood.SummedLikelihood()
    if hasKey(config,'ComponentAnalysis') == True:
        # Create one obs instance for each component
        configs  = [None]*4
        Fits     = [None]*4
        Analyses = [None]*4
        if isKey(config['ComponentAnalysis'],'FrontBack') == 'yes':
            from enrico.data import fermievtypes
            mes.info("Breaking the analysis in Front/Back events")
            # Set Summed Likelihood to True
            oldxml = config['file']['xml']
            for k,TYPE in enumerate(["FRONT", "BACK"]):
                configs[k] = ConfigObj(config)
                configs[k]['event']['evtype'] = fermievtypes[TYPE]
                try:
                    Analyses[k] = Analysis(folder, configs[k], \
                        configgeneric=config,\
                        tag=TYPE, verbose = verbose)
                    if not(xmlfile ==""): Analyses[k].obs.xmlfile = xmlfile
                    Fits[k] = Analyses[k].CreateLikeObject()
                    Fit.addComponent(Fits[k])
                except RuntimeError,e:
                    if 'RuntimeError: gtltcube execution failed' in str(e):
                        mes.warning("Event type %s is empty! Error is %s" %(TYPE,str(e)))
            FitRunner = Analyses[0]

    EUnBinned = config['ComponentAnalysis']['EUnBinned']
    emintotal = float(config['energy']['emin'])
    emaxtotal = float(config['energy']['emax'])

    evtnum = [config["event"]["evtype"]] #for std analysis
    evtold = evtnum[0] #for std analysis
 
    # Create one obs instance for each component
    if isKey(config['ComponentAnalysis'],'FrontBack') == 'yes':
        evtnum = [1, 2]
        config['analysis']['likelihood'] = "binned"
    if isKey(config['ComponentAnalysis'],'PSF') == 'yes':
        evtnum = [4,8,16,32]
        config['analysis']['likelihood'] = "binned"
    if isKey(config['ComponentAnalysis'],'EDISP') == 'yes':
        evtnum = [64,128,256,521]
        config['analysis']['likelihood'] = "binned"
    oldxml = config['file']['xml']
    for k,evt in enumerate(evtnum):
        config['event']['evtype'] = evt
        config["file"]["xml"] = oldxml.replace(".xml","_"+typeirfs[evt]+".xml").replace("_.xml",".xml")

        if EUnBinned>emintotal and EUnBinned<emaxtotal:
            mes.info("Breaking the analysis in Binned (low energy) and Unbinned (high energies)")
            analysestorun = ["lowE","highE"]

            for k,TYPE in enumerate(analysestorun):
                tag = TYPE
                if typeirfs[evt] != "" : tag += "_"+typeirfs[evt]# handle name of fits file

                # Tune parameters
                if TYPE is "lowE":
                    config['energy']['emin'] = emintotal
                    config['energy']['emax'] = min(config['energy']['emax'],EUnBinned)
                    config['analysis']['likelihood'] = "binned"
                    config['analysis']['ComputeDiffrsp'] = "no"
                elif TYPE is "highE":
                    config['energy']['emin'] = max(config['energy']['emin'],EUnBinned)
                    config['energy']['emax'] = emaxtotal
                    config['analysis']['likelihood'] = "unbinned"
                    config['analysis']['ComputeDiffrsp'] = "yes"

                Analyse = Analysis(folder, config, \
                    configgeneric=config,\
                    tag=TYPE,\
                    verbose=verbose)


                Fit_component = Analyse.CreateLikeObject()
                Fit.addComponent(Fit_component)
            FitRunner = Analyse
            FitRunner.obs.Emin = emintotal
            FitRunner.obs.Emax = emaxtotal

        else:
            Analyse = Analysis(folder, config, \
                configgeneric=config,\
                tag=typeirfs[evt], verbose = verbose)

            if not(xmlfile ==""): Analyse.obs.xmlfile = xmlfile
            Fit_component = Analyse.CreateLikeObject()
            Fit.addComponent(Fit_component)
    FitRunner = Analyse

    config["event"]["evtype"] = evtold
    FitRunner.config = config

    return FitRunner,Fit

def run(infile):
    from enrico import utils
    from enrico import energybin
    from enrico.config import get_config
    from enrico import Loggin
    mes = Loggin.Message()

    """Run an entire Fermi analysis (spectrum) by reading a config file"""
    config = get_config(infile)
    folder = config['out']
    utils.mkdir_p(folder)

    FitRunner,Fit = GenAnalysisObjects(config)
    # create all the fit files and run gtlike
    FitRunner.PerformFit(Fit)
    sedresult = None

    #plot the SED and model map if possible and asked
    if float(config['UpperLimit']['TSlimit']) < Fit.Ts(config['target']['name']):
        if config['Spectrum']['ResultPlots'] == 'yes':
            from enrico.constants import SpectrumPath
            utils.mkdir_p("%s/%s/" %(config['out'],SpectrumPath))
            sedresult = FitRunner.ComputeSED(Fit,dump=True)
        else:
            sedresult = FitRunner.ComputeSED(Fit,dump=False)
        
        if (config['energy']['decorrelation_energy'] == 'yes'):
            #Update the energy scale to decorrelation energy
            mes.info('Setting the decorrelation energy as new Scale for the spectral parameters')
            spectrum = Fit[FitRunner.obs.srcname].funcs['Spectrum']
            modeltype = spectrum.genericName()
            genericName = Fit.model.srcs[FitRunner.obs.srcname].spectrum().genericName()

            varscale = None
            if genericName=="PowerLaw2":
                varscale = None
            elif genericName in ["PowerLaw", "PLSuperExpCutoff", "EblAtten::PLSuperExpCutoff"]:
                varscale = "Scale"
            elif genericName in ["LogParabola","EblAtten::LogParabola", \
                                 "BrokenPowerLaw", "EblAtten::BrokenPowerLaw"]:
                varscale = "Eb"

            if varscale is not None:
                spectrum.getParam(varscale).setValue(sedresult.decE)
                FitRunner.PerformFit(Fit)
            
    #Get and dump the target specific results
    Result = FitRunner.GetAndPrintResults(Fit)
    utils.DumpResult(Result, config)

    FitRunner.config['file']['parent_config'] = infile
    if config['Spectrum']['ResultPlots'] == 'yes' :
        outXml = utils._dump_xml(config)
        # the possibility of making the model map is checked inside the function
        FitRunner.ModelMap(outXml)
        FitRunner.config['Spectrum']['ResultParentPlots'] = "yes"
        plot_sed_fromconfig(get_config(infile),ignore_missing_bins=True)
    
    if config['Spectrum']['ResultParentPlots'] == "yes":
        plot_sed_fromconfig(get_config(config['file']['parent_config']),ignore_missing_bins=True) 

    #  Make energy bins by running a *new* analysis
    Nbin = config['Ebin']['NumEnergyBins']
    
    energybin.RunEbin(folder,Nbin,Fit,FitRunner,sedresult)

    del(sedresult)
    del(Result)
    del(FitRunner)

# @todo: Should this be a command line utility in bin?
if __name__ == '__main__':
    import sys
    from enrico import Loggin
    mes = Loggin.Message()
    try:
        infile = sys.argv[1]
    except:
        print('Usage: '+sys.argv[0]+' <config file name>')
        mes.error('Config file not found.')

    run(infile)
