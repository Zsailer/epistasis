import numpy as np
import pandas as pd
from sklearn.preprocessing import binarize

import epistasis.mapping
from epistasis.model_matrix_ext import get_model_matrix

from .base import BaseModel
from .power import EpistasisPowerTransform
from .classifiers import EpistasisLogisticRegression
from .utils import FittingError, XMatrixException

# Suppress an annoying error
import warnings
#warnings.filterwarnings(action="ignore", category=RuntimeWarning)

# Warn users that this is still experimental!
warnings.warn("\n\nWarning!\n"
              "--------\n"
              "\nThe EpistasisMixedRegression is *very* experimental and under \n" 
              "active development! Beware when using -- the API is likely to \n"
              "change rapidly.\n\n",
              FutureWarning)

class EpistasisMixedRegression(BaseModel):
    """A high-order epistasis regression that first classifies genotypes as
    viable/nonviable (given some threshold) and then estimates epistatic coefficients
    in viable phenotypes.

    Parameters
    ----------
    order : int
        Order of epistasis in model
    threshold : float
        value below which phenotypes are considered dead
    model_type : str
        type of epistasis model to use.
    epistasis_model : epistasis.models object
        Epistasis model to use.
    epistasis_classifier : epistasis.models.classifier
        Epistasis classifier to use.

    Keyword Arguments
    -----------------
    Keyword arguments are interpreted as intial guesses for the nonlinear function
    parameters. Must have the same name as parameters in the nonlinear function

    """
    def __init__(self, order, threshold, model_type="global",
        epistasis_model=EpistasisPowerTransform,
        epistasis_classifier=EpistasisLogisticRegression,
        **p0):

        ### Set model specs.
        self.order = order
        self.threshold = threshold
        self.model_type = model_type

        ### Initialize the epistasis model
        self.Model = epistasis_model(order=self.order,
            model_type=self.model_type, **p0)

        ### Initialize the epistasis classifier
        # Hardcode the classifier model as a first order model.
        classifier_order = 1
        
        self.Classifier = epistasis_classifier(
            threshold=self.threshold,
            order=classifier_order,
            model_type=self.model_type)

    def add_gpm(self, gpm):
        """ Attach a GenotypePhenotypeMap object to the epistasis model.

        Also exposes APIs that are only accessible with a GenotypePhenotypeMap
        attached to the model.
        """
        super(EpistasisMixedRegression, self).add_gpm(gpm)
        self.Model.add_gpm(gpm)
        self.Classifier.add_gpm(gpm)

    def fit(self, X='obs', y='obs', use_widgets=False, **kwargs):
        """Fit mixed model in two parts. 1. Use Classifier to predict the
        class of each phenotype (Dead/Alive). 2. Fit epistasis Model.
        
        Do to the nature of the mixed model, the fit method is less flexible than 
        models in this package. This model requires that a GenotypePhenotypeMap
        object be attached.

        X must be:
            
            - 'obs' : Uses `gpm.binary.genotypes` to construct X. If genotypes are missing
                they will not be included in fit. At the end of fitting, an epistasis map attribute
                is attached to the model class.
            - 'complete' : Uses `gpm.binary.complete_genotypes` to construct X. All genotypes
                missing from the data are included. Warning, will break in most fitting methods.
                At the end of fitting, an epistasis map attribute is attached to the model class.
            - 'fit' : a previously defined array/dataframe matrix. Prevents copying for efficiency.

        y must be:
            - 'obs' : Uses `gpm.binary.phenotypes` to construct y. If phenotypes are missing
                they will not be included in fit. 
            - 'complete' : Uses `gpm.binary.complete_genotypes` to construct X. All genotypes
                missing from the data are included. Warning, will break in most fitting methods.
            - 'fit' : a previously defined array/dataframe matrix. Prevents copying for efficiency.


        Parameters
        ----------
        use_widgets : bool (default=False)
            If True, turns nonlinear parameters into ipywidgets.
        
        Keyword Arguments
        -----------------
        Keyword arguments are read as parameters to the nonlinear scale fit.
        """
        ######## Sanity checks on input.
        if hasattr(self, "gpm") is False:
            raise FittingError("A GenotypePhenotypeMap must be attached to this model.")
    
        # Make sure X and y strings match
        if type(X) == str and type(y) == str and X != y:
            raise FittingError("Any string passed to X must be the same as any string passed to y. "
                           "For example: X='obs', y='obs'.")
            
        # Else if both are arrays, check that X and y match dimensions.
        elif type(X) != str and type(y) != str and X.shape[0] != y.shape[0]:
            raise FittingError("X dimensions {} and y dimensions {} don't match.".format(X.shape[0], y.shape[0]))
            
        ######## Handle y.
        
        # Get pobs for nonlinear fit.
        if type(y) is str and y in ["obs", "complete"]:            
            pobs = self.gpm.binary.phenotypes
        # Else, numpy array or dataframe
        elif type(y) == np.array or type(y) == pd.Series:
            pobs = y
        else:
            raise FittingError("y is not valid. Must be one of the following: 'obs', 'complete', "
                           "numpy.array", "pandas.Series")    
            
        ######## Handle X
        self.Classifier.fit(X=X, y=y)
    
        # Use model to infer dead phenotypes
        ypred = self.Classifier.predict(X="fit")
        
        # Build an X matrix for the Epistasis model.
        x = self.Model.add_X(X="obs")
        
        # Subset the data (and x matrix) to only include alive genotypes/phenotypes
        y_subset = pobs[ypred==1]
        y_subset = y_subset.reset_index(drop=True)
        x_subset = x[ypred==1,:]     

        # Fit model to the alive phenotype supset
        out = self.Model.fit(X=x_subset, y=y_subset, use_widgets=use_widgets, **kwargs)
        
        return out        

    def plot_fit(self):
        """Plots the observed phenotypes against the additive model phenotypes"""        
        padd = self.Additive.predict()
        pobs = self.gpm.phenotypes
        fig, ax = plt.subplots(figsize=(3,3))
        ax.plot(padd, pobs, '.b')
        plt.show()
        return fig, ax    

    def predict(self, X="complete"):
        """Predict phenotypes given a model matrix. Constructs the predictions in
        two steps. 1. Use X to predict quantitative phenotypes. 2. Predict phenotypes
        classes using the Classifier. Xfit for the classifier is truncated to the
        order given by self.Classifier.order

        Return
        ------
        X : array
            Model matrix.
        ypred : array
            Predicted phenotypes.
        """
        # Predict the phenotypes from the epistasis model first.
        ypred = self.Model.predict(X=X)
        
        # Then predict the classes.
        yclasses = self.Classifier.predict(X=X)
        
        # Set any 0-class phenotypes to a value of 0
        ypred[yclasses==0] = 0
        
        return ypred
        
    def hypothesis(self, X=None, thetas=None):
        """Return a model's output with the given model matrix X and coefs."""
        # Use thetas to predict the probability of 1-class for each phenotype.
        if thetas is None:
            thetas = self.thetas

        thetas1 = thetas[0:len(self.Classifier.coef_[0])]
        thetas2 = thetas[len(self.Classifier.coef_[0]):]

        # 1. Class probability given the coefs
        proba = self.Classifier.hypothesis(thetas=thetas1)
        classes = np.ones(len(proba))
        classes[proba<0.5] = 0

        # 2. Determine ymodel given the coefs.
        y = self.Model.hypothesis(thetas=thetas2)
        y[classes==0] = 0
        return y

    def lnlikelihood(self, X=None, ydata=None, yerr=None, thetas=None):
        """Calculate the log likelihood of data, given a set of model coefficients.

        Parameters
        ----------
        X : 2d array
            model matrix
        yerr: array
            uncertainty in data
        thetas : array
            array of model coefficients

        Returns
        -------
        lnlike : float
            log-likelihood of the data given the model.
        """
        ### Data
        if ydata is None:
            ydata = self.gpm.phenotypes
            yerr = self.gpm.std.upper
        # Binarize the data
        ybin = binarize(ydata, threshold=self.threshold)[0]#np.ones(len(y_class_prob))

        if thetas is None:
            thetas = self.thetas

        thetas1 = thetas[0:len(self.Classifier.coef_[0])]
        thetas2 = thetas[len(self.Classifier.coef_[0]):]

        # 1. Class probability given the coefs
        Xclass = self.Xclass
        y_class_prob = self.Classifier.hypothesis(X=Xclass, thetas=thetas1)
        classes = np.ones(len(y_class_prob))
        classes[y_class_prob<0.5] = 0

        # 2. Determine ymodel given the coefs.
        if X is None:
            X = self.Xfit

        ymodel = self.Model.hypothesis(X=X,thetas=thetas2)
        ymodel[classes==0] = 0

        #ymodel = np.multiply(ymodel, classes)

        ### log-likelihood of logit model
        lnlikelihood = ybin * np.log(y_class_prob) + (1 - ybin) * np.log(1-y_class_prob)

        ### log-likelihood of the epistasis model
        inv_sigma2 = 1.0/(yerr**2)
        lngaussian = (ydata-ymodel)**2*inv_sigma2 - np.log(inv_sigma2)
        lnlikelihood[ybin==1] = np.add(lnlikelihood[ybin==1], lngaussian[ybin==1])
        lnlikelihood = -0.5 * sum(lnlikelihood)

        # If log-likelihood is infinite, set to negative infinity.
        if np.isinf(lnlikelihood):
            return -np.inf

        return lnlikelihood

    @property
    def thetas(self):
        """1d array of all coefs in model. The classifier coefficients are first
        in the array, then the model coefficients. See the thetas attributes of
        the input classifier/epistasis models to determine what is included in this
        combined array.
        """
        return np.concatenate((self.Classifier.thetas, self.Model.thetas))
