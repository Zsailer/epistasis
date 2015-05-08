# ------------------------------------------------------------
# Imports
# ------------------------------------------------------------

import itertools as it
import numpy as np
from scipy.linalg import hadamard
from sklearn.linear_model import LinearRegression
from collections import OrderedDict

# ------------------------------------------------------------
# Local imports
# ------------------------------------------------------------
from epistasis.mapping.epistasis import EpistasisMap
from epistasis.regression_ext import generate_dv_matrix
from epistasis.utils import epistatic_order_indices

# ------------------------------------------------------------
# Unique Epistasis Functions
# ------------------------------------------------------------   

def hadamard_weight_vector(genotypes):
    """ Build the hadamard weigth vector"""
    l = len(genotypes)
    n = len(genotypes[0])
    weights = np.zeros((l, l), dtype=float)
    for g in range(l):
        epistasis = float(genotypes[g].count("1"))
        weights[g][g] = ((-1)**epistasis)/(2**(n-epistasis))    
    return weights    

def cut_interaction_labels(labels, order):
    """ Cut off interaction labels at certain order of interactions. """
    return [l for l in labels if len(l) <= order]
    
# ------------------------------------------------------------
# Epistasis Mapping Classes
# ------------------------------------------------------------
class GenericModel(EpistasisMap):
    
    def __init__(self, wildtype, genotypes, phenotypes, phenotype_errors=None, log_phenotypes=False):
        """ Populate an Epistasis mapping object. """
        
        super(GenericModel, self).__init__()
        self.genotypes = genotypes
        self.wildtype = wildtype
        self.log_transform = log_phenotypes
        self.phenotypes = phenotypes
        if phenotype_errors is not None:
            self.errors = phenotype_errors
            
    def get_order(self, order, errors=False, label="genotype"):
        """ Return a dict of interactions to values of a given order. """
        
        # get starting index of interactions
        if order > self.order:
            raise Exception("Order argument is higher than model's order")
            
        # Determine the indices of this order of interactions.
        start, stop = epistatic_order_indices(self.length,order)
        # Label type.
        if label == "genotype":
            keys = self.Interactions.genotypes
        elif label == "keys":
            keys = self.Interactions.keys
        else:
            raise Exception("Unknown keyword argument for label.")
        
        # Build dictionary of interactions
        stuff = OrderedDict(zip(keys[start:stop], self.Interactions.values[start:stop]))
        if errors:
            errors = OrderedDict(zip(keys[start:stop], self.Interactions.errors[start:stop]))
            return stuff, errors
        else:
            return stuff


class LocalEpistasisModel(GenericModel):
        
    def __init__(self, wildtype, genotypes, phenotypes, phenotype_errors=None, log_phenotypes=False):
        """ Create a map of the local epistatic effects using expanded mutant 
            cycle approach.
            
            i.e.
            Phenotype = K_0 + sum(K_i) + sum(K_ij) + sum(K_ijk) + ...
            
            Parameters:
            ----------
            geno_pheno_dict: OrderedDict
                Dictionary with keys=ordered genotypes by their binary value, 
                mapped to their phenotypes.
            log_phenotypes: bool (default=True)
                Log transform the phenotypes for additivity.
        """
        # Populate Epistasis Map
        super(LocalEpistasisModel, self).__init__(wildtype, genotypes, phenotypes, phenotype_errors=phenotype_errors, log_phenotypes=log_phenotypes)
        self.order = self.length
        # Generate basis matrix for mutant cycle approach to epistasis.
        self.X = generate_dv_matrix(self.Binary.genotypes, self.Interactions.labels)
        self.X_inv = np.linalg.inv(self.X)
        
    def estimate_interactions(self):
        """ Estimate the values of all epistatic interactions using the expanded
            mutant cycle method to order=number_of_mutations.
        """
        self.Interactions.values = np.dot(self.X_inv, self.Binary.phenotypes)
        
    def estimate_error(self):
        """ Estimate the error of each epistatic interaction by standard error 
            propagation of the phenotypes through the model.
        """
        if self.log_transform is True:
            # If log-transformed, fit assymetric errorbars correctly
            upper = np.sqrt(np.dot(self.X, self.Binary.errors[0]**2))
            lower = np.sqrt(np.dot(self.X, self.Binary.errors[1]**2))
            self.Interactions.errors = np.array((lower,upper))
        else:
            # Errorbars are symmetric, so only one column for errors is necessary
            self.Interactions.errors = np.sqrt(np.dot(self.X, self.Binary.errors**2))
    
class GlobalEpistasisModel(GenericModel):
    
    def __init__(self, wildtype, genotypes, phenotypes, phenotype_errors=None, log_phenotypes=False):
        """ Create a map of the global epistatic effects using Hadamard approach.
            This is the related to LocalEpistasisMap by the discrete Fourier 
            transform of mutant cycle approach. 
        """
        # Populate Epistasis Map
        super(GlobalEpistasisModel, self).__init__(wildtype, genotypes, phenotypes, phenotype_errors, log_phenotypes)
        self.order = self.length
        # Generate basis matrix for mutant cycle approach to epistasis.
        self.weight_vector = hadamard_weight_vector(self.Binary.genotypes)
        self.X = hadamard(2**self.length)
        
    def estimate_interactions(self):
        """ Estimate the values of all epistatic interactions using the hadamard
        matrix transformation.
        """
        self.Interactions.values = np.dot(self.weight_vector,np.dot(self.X, self.Binary.phenotypes))
        
    def estimate_error(self):
        """ Estimate the error of each epistatic interaction by standard error 
            propagation of the phenotypes through the model.
        """
        if self.log_transform is True:
            # If log-transformed, fit assymetric errorbars correctly
            # upper and lower are unweighted tranformations
            upper = np.sqrt(np.dot(abs(self.X), self.Binary.errors[0]**2))
            lower = np.sqrt(np.dot(abs(self.X), self.Binary.errors[1]**2))
            self.Interactions.errors = np.array((np.dot(self.weight_vector, lower), np.dot(self.weight_vector, upper)))
        else:
            unweighted = np.sqrt(np.dot(abs(self.X), self.Binary.errors**2))
            self.Interactions.errors = np.dot(self.weight_vector, unweighted)
            
    
class ProjectedEpistasisModel(GenericModel):
    
    def __init__(self, wildtype, genotypes, phenotypes, regression_order, phenotype_errors=None, log_phenotypes=False):
        """ Create a map from local epistasis model projected into lower order
            order epistasis interactions. Requires regression to estimate values. 
        """
        # Populate Epistasis Map
        super(ProjectedEpistasisModel, self).__init__(wildtype, genotypes, phenotypes, phenotype_errors, log_phenotypes)
        
        # Generate basis matrix for mutant cycle approach to epistasis.
        self.order = regression_order
       
        self.X = generate_dv_matrix(self.Binary.genotypes, self.Interactions.labels)
        
        # Regression properties
        self.regression_model = LinearRegression(fit_intercept=False)
        self.error_model = LinearRegression(fit_intercept=False)
        self.score = None
        
        
    def estimate_interactions(self):
        """ Estimate the values of all epistatic interactions using the expanded
            mutant cycle method to any order<=number of mutations.
        """
        self.regression_model.fit(self.X, self.Binary.phenotypes)
        self.score = self.regression_model.score(self.X, self.Binary.phenotypes)
        self.Interactions.values = self.regression_model.coef_
        
        
    def estimate_error(self):
        """ Estimate the error of each epistatic interaction by standard error 
            propagation of the phenotypes through the model.
        """
        if self.log_transform is True:
            interaction_errors = np.empty((2,len(self.Interactions.labels)), dtype=float)
            for i in range(len(self.Interactions.labels)):
                n = len(self.Interactions.labels[i])
                interaction_errors[0,i] = np.sqrt(n*self.Binary.errors[0,i]**2)
                interaction_errors[1,i] = np.sqrt(n*self.Binary.errors[1,i]**2)
            self.Interactions.errors = interaction_errors        
        else:
            interaction_errors = np.empty(len(self.Interactions.labels), dtype=float)
            for i in range(len(self.Interactions.labels)):
                n = len(self.Interactions.labels[i])
                interaction_errors[i] = np.sqrt(n*self.Binary.errors[i]**2)
            self.Interactions.errors = interaction_errors
        