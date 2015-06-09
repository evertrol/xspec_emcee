#!/usr/bin/env python

"""
Use EMCEE to do MCMC in Xspec.
Jeremy Sanders 2012

Requires Python 2.7+, numpy, scipy and emcee
"""

import sys
import argparse
import time
import re

import numpy as N
import emcee

import xspec_pool

def getInitialParameters(parameters, nwalkers):
    """Construct list of initial parameter values for each walker."""
    p0 = []
    for walker in xrange(nwalkers):
        pwalker = []
        # for each walker, use initial parameters based on parameter
        # and delta parameter
        for par in parameters:
            width = par['val_delta']
            swidth = par['val_sigma']*0.1
            if swidth > 0 and swidth < width:
                # use sigma if delta is badly adjusted
                width = swidth

            v = N.random.normal(par['val_init'], width)
            # clip to hard range
            v = N.clip(v, par['val_hardmin'], par['val_hardmax'])
            pwalker.append(v)
        p0.append( N.array(pwalker) )
    return N.array(p0)

def expandSystems(systems):
    """Allow system*N syntax in systems."""
    out = []
    for s in systems:
        m = re.match(r'([A-Za-z0-9]+)\*([0-9]+)', s)
        if m:
            out += [m.group(1)]*int(m.group(2))
        else:
            out.append(s)
    return out

def doMCMC(xcm, nwalkers=100, nburn=100, niters=1000, systems = ['localhost'],
           outchain='out.dat', outnpz='out.npz', debug=False,
           continuerun=False, autosave=True,
           nochdir=False, initialparameters=None,
           lognorm=False, chunksize=4):
    """Do the actual MCMC process."""

    # pool controls xspecs and parameters
    # this should be a multiprocessing.Pool, but we implement
    # our own pool as it is much more reliable
    pool = xspec_pool.XspecPool(
        xcm, expandSystems(systems), debug=debug, nochdir=nochdir,
        lognorm=lognorm, chunksize=chunksize)

    if not initialparameters:
        p0 = getInitialParameters(pool.parameters, nwalkers)
    else:
        print "Loading initial parameters from", initialparameters
        p0 = N.loadtxt(initialparameters)

    ndims = p0.shape[1]

    # sample the mcmc
    sampler = emcee.EnsembleSampler(nwalkers, ndims, None, pool=pool)

    if not continuerun and nburn > 0:
        # burn in
        print "Burn in period started"
        pos, prob, state = sampler.run_mcmc(p0, nburn)
        sampler.reset()
        print "Burn in period finished"
    else:
        # no burn in
        state = None
        pos = p0

    if not continuerun:
        chain = N.zeros( (nwalkers, niters, ndims) )
        lnprob = N.zeros( (nwalkers, niters) )
        start = 0
    else:
        print "Continuing from existing chain in", outnpz

        # load old chain and probabilities
        f = N.load(outnpz)
        chain = f['chain']
        lnprob = f['lnprobability']
        del f

        # where to start writing into new chain
        start = chain.shape[1]
        pos = N.array(chain[:, -1, :])
        print "Restarting at iteration", start

        # construct new chain with new blank entries
        blankchain = N.zeros( (nwalkers, niters-start, ndims) )
        blankprob = N.zeros( (nwalkers, niters-start) )
        chain = N.concatenate((chain, blankchain), axis=1)
        lnprob = N.concatenate((lnprob, blankprob), axis=1)

    # iterator interface allows us to trap ctrl+c and know where we are
    lastsave = time.time()
    index = start
    try:
        for p, l, s in sampler.sample(
            pos, rstate0=state, storechain=False,
            iterations=niters-start):

            chain[:, index, :] = p
            lnprob[:, index] = l
            index += 1

            if autosave and time.time() - lastsave > 60*10:
                # every 10 minutes, save state of chain
                lastsave = time.time()
                writeNpz(outnpz, chain, lnprob, index)

    except KeyboardInterrupt:
        print "Ctrl+C pressed - saving current state of chain in .npz"
    else:
        writeXSpecChain(outchain, chain, lnprob, pool.parlist, pool.paridxs)

    writeNpz(outnpz, chain, lnprob, index)

def writeXSpecChain(filename, chain, lnprob, params, paridxs):
    """Write an xspec text chain file."""

    print "Writing", filename
    with open(filename, 'w') as f:

        f.write('! Markov chain file generated by xspec "chain" command.\n')
        f.write('!    Do not modify, else file may not reload properly.\n')
        length = chain.shape[0] * chain.shape[1]
        width = chain.shape[2]
        f.write('!Length: %i  Width: %i\n' % (length, width+1))

        chain = N.column_stack((N.reshape(chain, (length, width)),
                                N.reshape(-lnprob*2, (length, 1))))
        # undo log of some parameters
        for i, idx in enumerate(paridxs):
            if params[idx-1]['log']:
                chain[:,i] = 10**chain[:,i]

        # header for contents of file
        hdr = []
        for idx in paridxs:
            hdr.append("%i %s %s" % (
                    idx, params[idx-1]['name'],
                    "0" if params[idx-1]['unit'] == ""
                    else params[idx-1]['unit']))

        hdr.append("Chi-Squared")
        f.write('!%s\n' % ' '.join(hdr))

        for line in chain:
            fmt = '\t'.join(['%g']*len(line))
            f.write( fmt % tuple(line) + '\n' )

def writeNpz(filename, chain, lnprob, maxindex):
    """Write output NPZ file."""

    print "Writing %s (%i iterations)" % (filename, maxindex)

    if maxindex < chain.shape[1]:
        chain = N.array(chain)[:, :maxindex, :]
        lnprob = N.array(lnprob)[:, :maxindex]

    N.savez( filename,
             chain = chain,
             lnprobability = lnprob,
             )

def main():
    """Main program."""

    p = argparse.ArgumentParser(
        description="Xspec MCMC with EMCEE. Jeremy Sanders 2012.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    p.add_argument("xcm", metavar="XCM",
                   help="Input XCM file")
    p.add_argument("--niters", metavar="N", type=int, default=5000,
                   help="Number of iterations")
    p.add_argument("--nburn", metavar="N", type=int, default=500,
                   help="Number of burn iterations")
    p.add_argument("--nwalkers", metavar="N", type=int, default=50,
                   help="Number of walkers")
    p.add_argument("--systems", default="localhost", metavar="LIST",
                   help="Space separated list of systems to run on")
    p.add_argument("--output-npz", default="emcee.npz", metavar="FILE",
                   help="Output NPZ file")
    p.add_argument("--output-chain", default="emcee.chain", metavar="FILE",
                   help="Output text file")
    p.add_argument("--continue-run",  action="store_true", default=False,
                   help="Continue from an existing chain (in npz)")
    p.add_argument("--debug", action="store_true", default=False,
                   help="Create xspec log files")
    p.add_argument("--no-chdir", action="store_true", default=False,
                   help="Do not chdir to xcm file directory before execution")
    p.add_argument("--initial-parameters", metavar="FILE",
                   help="Provide initial parameters")
    p.add_argument("--log-norm", action="store_true", default=False,
                   help="log norm values during MCMC")
    p.add_argument('--chunk-size', metavar='N', type=int, default=4,
                   help='Number of sets of parameters to pass to xspec')

    args = p.parse_args()

    print "Starting MCMC"
    sampler = doMCMC( args.xcm,
                      systems = args.systems.split(),
                      nwalkers = args.nwalkers,
                      nburn = args.nburn,
                      niters = args.niters,
                      outchain = args.output_chain,
                      outnpz = args.output_npz,
                      continuerun = args.continue_run,
                      debug = args.debug,
                      nochdir = args.no_chdir,
                      initialparameters = args.initial_parameters,
                      lognorm = args.log_norm,
                      chunksize = args.chunk_size,
                      )

    print "Done"

if __name__ == '__main__':
    main()
