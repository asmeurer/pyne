"""
This module contains functions for mesh-based Monte Carlo variance reduction.
"""

from itertools import izip
from warnings import warn
from pyne.utils import QAWarning

import numpy as np

warn(__name__ + " is not yet QA compliant.", QAWarning)

try:
    from itaps import iMesh, iBase, iMeshExtensions
except ImportError:
    warn("the PyTAPS optional dependency could not be imported. "
        "Some aspects of the variance reduction module may be incomplete.", 
        QAWarning)

from mesh import Mesh
from mesh import MeshError
from mesh import IMeshTag
from mcnp import Wwinp

def cadis(adj_flux_mesh, adj_flux_tag, q_mesh, q_tag, 
          ww_mesh, ww_tag, q_bias_mesh, q_bias_tag, beta=5):
    """This function reads PyNE Mesh objects tagged with adjoint fluxes and 
    unbiased source densities and outputs PyNE Meshes of weight window lower 
    bounds and biased source densities as computed by the Consistant 
    Adjoint-Driven Importance Sampling (CADIS) method [1]. Note that values can be
    stored on the same Mesh object, all different Mesh objects, or any
    combination in between. Meshes can be structured or unstructured. 
    Note that this function is suitable for Forward Weighted (FW) CADIS as well,
    the only difference being the adjoint source used for the estimation of the 
    adjoint flux.

    [1] Haghighat, A. and Wagner, J. C., "Monte Carlo Variance Reduction with 
        Deterministic Importance Functions," Progress in Nuclear Energy, 
        Vol. 42, No. 1, pp. 25-53, 2003.

    Parameters
    ----------
    adj_flux_mesh : PyNE Mesh object
        The mesh containing the adjoint fluxes.
    adj_flux_tag : string
        The name of the adjoint flux tag on adj_mesh.
    q_mesh : PyNE Mesh object
        The mesh containing the unbiased source density.
    q_tag : string
        The name of the source density tag on q_mesh.
    ww_mesh : PyNE Mesh object
        The mesh to store the output weight window mesh.
    ww_tag : string
        Name of the tag to store output weight window values on ww_mesh.
    q_bias_mesh : PyNE Mesh object
        The mesh to store the output biased source density mesh.
    q_bias_tag : PyNE Mesh object 
        Name of the tag to store output weight window values on q_bias_mesh.
    beta : float
        The ratio of the weight window upper bound to the weight window lower
        bound. The default value is 5: the value used in MCNP. 
    """
    
    # find number of energy groups
    e_groups = adj_flux_mesh.mesh.getTagHandle(adj_flux_tag)[list(
                   adj_flux_mesh.mesh.iterate(iBase.Type.region, 
                                              iMesh.Topology.all))[0]]
    e_groups = np.atleast_1d(e_groups)
    num_e_groups = len(e_groups)

    # verify source (q) mesh has the same number of energy groups
    q_e_groups = q_mesh.mesh.getTagHandle(q_tag)[list(
                      q_mesh.mesh.iterate(iBase.Type.region, 
                                          iMesh.Topology.all))[0]]
    q_e_groups = np.atleast_1d(q_e_groups)
    num_q_e_groups = len(q_e_groups)

    if num_q_e_groups != num_e_groups:
        raise TypeError("{0} on {1} and {2} on {3} "
            "must be of the same dimension".format(adj_flux_mesh, adj_flux_tag, 
                                                   q_mesh, q_tag))

    # create volume element (ve) iterators
    adj_ves = adj_flux_mesh.mesh.iterate(iBase.Type.region, iMesh.Topology.all)
    q_ves = q_mesh.mesh.iterate(iBase.Type.region, iMesh.Topology.all)

    # calculate total source strength
    q_tot = 0
    for q_ve in q_ves:
        q_tot += np.sum(q_mesh.mesh.getTagHandle(q_tag)[q_ve]) \
                 *q_mesh.elem_volume(q_ve)

    q_ves.reset()

    # calculate the total response per source particle (R)
    R = 0
    for adj_ve, q_ve in zip(adj_ves, q_ves):
        adj_flux = adj_flux_mesh.mesh.getTagHandle(adj_flux_tag)[adj_ve]
        adj_flux = np.atleast_1d(adj_flux)
        q = q_mesh.mesh.getTagHandle(q_tag)[q_ve]
        q = np.atleast_1d(q)

        vol = adj_flux_mesh.elem_volume(adj_ve)
        for i in range(0, num_e_groups):
            R += adj_flux[i]*q[i]*vol/q_tot

    # generate weight windows and biased source densities using R
    tag_ww = ww_mesh.mesh.createTag(ww_tag, num_e_groups, float)
    ww_ves = ww_mesh.mesh.iterate(iBase.Type.region, iMesh.Topology.all)
    tag_q_bias = q_bias_mesh.mesh.createTag(q_bias_tag, num_e_groups, float) 
    q_bias_ves = q_bias_mesh.mesh.iterate(iBase.Type.region, iMesh.Topology.all)
    # reset previously created iterators
    q_ves.reset()
    adj_ves.reset()

    for adj_ve, q_ve, ww_ve, q_bias_ve in izip(adj_ves, q_ves, 
                                               ww_ves, q_bias_ves):
        adj_flux = adj_flux_mesh.mesh.getTagHandle(adj_flux_tag)[adj_ve]
        adj_flux = np.atleast_1d(adj_flux)
        q = q_mesh.mesh.getTagHandle(q_tag)[q_ve]
        q = np.atleast_1d(q)

        tag_q_bias[q_bias_ve] = [adj_flux[i]*q[i]/q_tot/R 
                                 for i in range(num_e_groups)]

        tag_ww[ww_ve] = [R/(adj_flux[i]*(beta + 1.)/2.) 
                         if adj_flux[i] != 0.0 else 0.0 
                         for i in range(num_e_groups)]


def magic(meshtally, tag_name, tag_name_error, tolerance, null_value):
    """This function reads a PyNE MeshTally and preforms the MAGIC algorithm 
    and returns the resulting weight window mesh.

    Parameters:
    -----------
        tally : a single PyNE MeshTally obj
        tag_name : string
            The meshtally tag_name (example: n_result or n_total_result). If 
            the string "total" exists in the name, then it addressed as a
            single total energy bin. If not, then different energy bins.
        tag_name_error : string
            The meshtally tag_name for the error associated with provided 
            tag_name. Example: n_rel_error
        tolerance : float
            The maximum relative error allowable for the MAGIC algorithm to 
            create a weight window lower bound for for a given mesh volume 
            element for the intial weight window lower bound generation, or 
            overwrite preexisting weight window lower bounds for subsequent 
            iterations. 
        null_value : float
            The weight window lower bound value that is assigned to mesh volume
             elements where the relative error on flux exceeds the tolerance.
    """
    
    tolerance = float(tolerance)
    null_value = float(null_value)
    
    # Convert particle name to the recognized abbreviation
    if meshtally.particle == "neutron":
        meshtally.particle = "n"
    elif meshtally.particle == "photon":
        meshtally.particle = "p"
    elif meshtally.particle == "electron":
        meshtally.particle = "e"
    
    # Determine if total energy or separate energy bins
    if "total" in tag_name:
        total = True
    elif len(meshtally.e_bounds) == 2:
        total = True
    else:
        total = False
    
    # create tag values
    meshtally.vals = IMeshTag(1, float, mesh=meshtally, name=tag_name)
    meshtally.errors = IMeshTag(1, float, mesh=meshtally, name=tag_name_error)  
    
    if total:
        meshtally.ww_x = IMeshTag(1, float, name="ww_{0}".format(meshtally.particle))
        root_tag = meshtally.mesh.createTag(
            "{0}_e_upper_bounds".format(meshtally.particle),1, float)
        root_tag[meshtally.mesh.rootSet] = np.max(meshtally.e_bounds[:])
        
        max_val = np.max(meshtally.vals[:])

        ww = []
        for ve, flux in enumerate(meshtally.vals[:]):
            if meshtally.errors[ve] > tolerance:
                ww.append(null_value)
            else:
                ww.append(flux/(2.0*max_val))
        
    else:
        meshtally.ww_x = IMeshTag(len(meshtally.e_bounds)-1, float, name="ww_{0}".format(meshtally.particle))
        root_tag = meshtally.mesh.createTag(
                    "{0}_e_upper_bounds".format(meshtally.particle), 
                    len(meshtally.e_bounds)-1, 
                    float)
        root_tag[meshtally.mesh.rootSet] = meshtally.e_bounds[1:]
        
        # Determine the max values for each energy bin
        max_val = []
        for i in range(len(meshtally.e_bounds)-1):
            vals_in_e = []
            for ve, flux in enumerate(meshtally.vals[:]):
                vals_in_e.append(meshtally.vals[ve][i])
            
            max_val.append(np.max(vals_in_e))
        
        # Apply normalization to create weight windows
        ww = []
        for ve, flux_list in enumerate(meshtally.vals[:]):
            tally_list = meshtally.errors[ve]
            flux = []
            for i, value in enumerate(flux_list):
                if tally_list[i] > tolerance:
                    flux.append(null_value)
                else:    
                    flux.append(value/(2.0*max_val[i]))
            
            ww.append(flux)

    meshtally.ww_x[:] = ww
    
    # Create wwinp mesh
    wwinp = Wwinp()
    wwinp.read_mesh(meshtally.mesh)
