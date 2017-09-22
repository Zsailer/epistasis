import numpy as np
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

        # Set model specs.
        self.order = order
        self.threshold = threshold
        self.model_type = model_type
        self._Xbuilt = {}

        # Initialize the epistasis model
        self.Model = epistasis_model(order=self.order,
            model_type=self.model_type, **p0)

        # Initialize the epistasis classifier
        self.Classifier = epistasis_classifier(
            threshold=self.threshold,
            order=1,
            model_type=self.model_type)

    @property
    def Xbuilt(self):
        """
        Keys
        ----
        obs : the X matrix built by using the observe genotypes
        class : the X matrix used to fit the classifier
        fit : the X matrix used to fit the model
        complete : the X matrix built by using all possible genotypes
        predict : the X matrix used last to predict phenotypes from model.
        """
        return self._Xbuilt

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

        X must be:
            
            - 'obs' : Uses `gpm.binary.genotypes` to construct X. If genotypes are missing
                they will not be included in fit. At the end of fitting, an epistasis map attribute
                is attached to the model class.
            - 'complete' : Uses `gpm.binary.complete_genotypes` to construct X. All genotypes
                missing from the data are included. Warning, will break in most fitting methods.
                At the end of fitting, an epistasis map attribute is attached to the model class.
            - numpy.ndarray : 2d array. Columns are epistatic coefficients, rows are genotypes.
            - pandas.DataFrame : Dataframe with columns labelled as epistatic coefficients, and
                rows labelled by genotypes.
                
                
        y must be:
            - 'obs' : Uses `gpm.binary.phenotypes` to construct y. If phenotypes are missing
                they will not be included in fit. 
            - 'complete' : Uses `gpm.binary.complete_genotypes` to construct X. All genotypes
                missing from the data are included. Warning, will break in most fitting methods.
            - 'fit' : a previously defined array/dataframe matrix. Prevents copying for efficiency.
            - numpy.array : 1 array. List of phenotypes. Must match number of rows in X.
            - pandas.DataFrame : Dataframe with columns labelled as epistatic coefficients, and
                rows labelled by genotypes.
        """
        
        ######## Sanity checks on input.
                
        # Make sure X and y strings match
        if type(X) == str and type(y) == str and X != y:
            raise FittingError("Any string passed to X must be the same as any string passed to y. "
                           "For example: X='obs', y='obs'.")
            
        # Else if both are arrays, check that X and y match dimensions.
        elif type(X) != str and type(y) != str and X.shape[1] != y.shape[0]:
            raise FittingError("X dimensions {} and y dimensions {} don't match.".format(X.shape[1], y.shape[0]))
            
        ######## Handle y.
        
        # Check if string.
        if type(y) is str and y in ["obs", "complete"]:            

            y = self.gpm.binary.phenotypes

        # Else, numpy array or dataframe
        elif type(y) != np.array and type(y) != pd.Series:
            
            raise FittiungError("y is not valid. Must be one of the following: 'obs', 'complete', "
                           "numpy.array", "pandas.Series")    
            
        ######## Handle X
        
        # Check if X has already been built. Avoid rebuilding if not necessary.
        try:

            Xclass = self.Xbuilt['class']
            Xfit = self.Xbuilt[X]
            self.Classifier.fit(X=Xclass, y=y)

        except (KeyError, TypeError):                        
            
            ### START WORKING HERE!!
            if type(X) == str:
                
                # Get rows for X matrix
                if X == "obs":
                    index = self.gpm.binary.genotypes
                elif X == "complete":
                    index = self.gpm.binary.complete_genotypes
                else:
                    raise XMatrixException("X string argument is not valid.")

                # It is hardcoded that Classifier is a first order model.
                # This model is not meant for high-order Classifier models. 
                classifier_order = 1
                sites = epistasis.mapping.mutations_to_sites(classifier_order, self.gpm.mutations)
                # Append an EpistasisMap to the Classifier.
                self.Classifier.epistasis = epistasis.mapping.EpistasisMap(sites,
                    order=classifier_order, model_type=self.model_type)
                
                # Build X matrix for classifier model
                self.Xclass = get_model_matrix(index, sites, model_type=self.model_type)            
                
                # Store X
                self._Xbuilt['class'] = self.Xclass

                # Fit Classifier model
                self.Classifier.fit(X=self.Xclass, y=y)
                
                # Reshape the Classifier's Scikit parameters to be an array, not column
                vals = self.Classifier.coef_.reshape((-1,))
                self.Classifier.epistasis.values = vals
                
                # Use the regressed model to predict classes for observed phenotypes phenotype.
                ypred = self.Classifier.predict(X=self.Xclass)
                    
                # --------------------------------------------------------
                # Part 2: fit nonlinear scale and any epistasis
                # --------------------------------------------------------
                
                # Build X matrix for epistasis model
                model_order = self.order
                sites = epistasis.mapping.mutations_to_sites(model_order, self.gpm.mutations)
                self.Model.epistasis = epistasis.mapping.EpistasisMap(sites,
                    order=model_order, model_type=self.model_type)    
                                            
                # Build X for epistasis model
                self.Xfit = get_model_matrix(index, sites, model_type=self.model_type)
                self.Xbuilt[X] = self.Xfit
                
                # Ignore phenotypes that are found "dead"
                y = y[ypred==1]
                y = y.reset_index(drop=True)
                self.Xfit = self.Xfit[ypred==1,:]
                self.Xbuilt["fit"] = X
                
                # Fit model
                out = self.Model.fit(X=self.Xfit, y=y, use_widgets=use_widgets, **kwargs)                

                if use_widgets:
                    return out
            
                # Point the EpistasisMap to the fitted-model coefficients. 
                self.Model.epistasis.values = self.Model.coef_
                # RSHAPE ?? self.Model.epistasis.values = self.Model.coef_.reshape((-1,)) 

            elif type(X) == np.ndarray or type(X) == pd.DataFrame:

                if X.shape[0] != len(y):
                    raise XMatrixException("X dimensions do no match y.")
            
                try:                    
                    # Use the regressed model to predict classes for observed phenotypes phenotype.
                    ypred = self.Classifier.predict(X=self.Xclass)
                except:
                    raise FittingError("This `fit` method cannot accept an array argument for X "
                                       "until the Classifier fit method is called.")
                                       
                if len(ypred) != len(y):
                    raise FittingError("len(y) is not compatible with the Classifier X matrix.")
                    
                # Ignore phenotypes that are found "dead"
                y = y[ypred==1]
                y = y.reset_index(drop=True)
                self.Xfit = self.Xfit[ypred==1,:]
                self.Xbuilt["fit"] = X
                
                # Fit model
                out = self.Model.fit(X=self.Xfit, y=y, use_widgets=use_widgets, **kwargs)                

                if use_widgets:
                    return out
            
                # Point the EpistasisMap to the fitted-model coefficients. 
                self.Model.epistasis.values = self.Model.coef_
                # RSHAPE ?? self.Model.epistasis.values = self.Model.coef_.reshape((-1,)) 
            
            else:
                raise XMatrixException("X is a not a valid datatype.")
                
                
                
        #         
        #         
        #         
        #         # --------------------------------------------------------
        #         # Part 2: fit epistasis
        #         # --------------------------------------------------------
        #         # Build X matrix for epistasis model
        #         order = self.order
        #         sites = epistasis.mapping.mutations_to_sites(order, self.gpm.mutations)
        #         self.Xfit = get_model_matrix(self.gpm.binary.genotypes, sites, model_type=self.model_type)
        # 
        #         # Ignore phenotypes that are found "dead"
        #         y = y[ypred==1]
        #         y = y.reset_index(drop=True)
        #         X = self.Xfit[ypred==1,:]
        # 
        #         # Fit model
        #         out = self.Model.fit(X=X, y=y, use_widgets=use_widgets, **kwargs)
        # 
        #         if use_widgets:
        #             return out
        # 
        #         # Append epistasis map to coefs
        #         self.Model.epistasis = epistasis.mapping.EpistasisMap(sites,
        #             order=order, model_type=self.model_type)
        #         self.Model.epistasis.values = self.Model.coef_.reshape((-1,))
        # 
        #         
        #     elif type(X) == np.ndarray or type(X) == pd.DataFrame:
        #         
        #         ## 
        #         pass
        #     
        #     
        #     else:
        #         raise XMatrixException("X is a not a valid datatype.")
        # 
        # if X is None:
        #     # --------------------------------------------------------
        #     # Part 1: classify
        #     # --------------------------------------------------------
        #     # Build X matrix for classifier
        #     order = 1
        #     sites = epistasis.mapping.mutations_to_sites(order, self.gpm.mutations)
        #     self.Xclass = get_model_matrix(self.gpm.binary.genotypes, sites, model_type=self.model_type)
        # 
        #     # Fit classifier
        #     self.Classifier.fit(X=self.Xclass, y=y)
        # 
        #     # Append epistasis map to coefs
        #     self.Classifier.epistasis = epistasis.mapping.EpistasisMap(sites,
        #         order=order, model_type=self.model_type)
        #         
        #     self.Classifier.epistasis.values = self.Classifier.coef_.reshape((-1,))
        #     ypred = self.Classifier.predict(X=self.Xclass)
        # 
        #     # --------------------------------------------------------
        #     # Part 2: fit epistasis
        #     # --------------------------------------------------------
        #     # Build X matrix for epistasis model
        #     order = self.order
        #     sites = epistasis.mapping.mutations_to_sites(order, self.gpm.mutations)
        #     self.Xfit = get_model_matrix(self.gpm.binary.genotypes, sites, model_type=self.model_type)
        # 
        #     # Ignore phenotypes that are found "dead"
        #     y = y[ypred==1]
        #     y = y.reset_index(drop=True)
        #     X = self.Xfit[ypred==1,:]
        # 
        #     # Fit model
        #     out = self.Model.fit(X=X, y=y, use_widgets=use_widgets, **kwargs)
        # 
        #     if use_widgets:
        #         return out
        # 
        #     # Append epistasis map to coefs
        #     self.Model.epistasis = epistasis.mapping.EpistasisMap(sites,
        #         order=order, model_type=self.model_type)
        #     self.Model.epistasis.values = self.Model.coef_.reshape((-1,))
        # 
        # else:
        #     # --------------------------------------------------------
        #     # Part 1: classify
        #     # --------------------------------------------------------
        #     self.Classifier.fit()
        #     ypred = self.Classifier.predict(X=self.Classifier.Xfit)
        # 
        #     # Ignore phenotypes that are found "dead"
        #     y = y[ypred==1]
        #     y = y.reset_index(drop=True)
        #     X = X[ypred==1,:]
        # 
        #     # --------------------------------------------------------
        #     # Part 2: fit epistasis
        #     # --------------------------------------------------------
        #     self.Model.fit(X=X, y=y, **kwargs)
        # 
        # return self


    def plot_fit(self):
        """Plots the observed phenotypes against the additive model phenotypes"""        
        padd = self.Additive.predict()
        pobs = self.gpm.phenotypes
        fig, ax = plt.subplots(figsize=(3,3))
        ax.plot(padd, pobs, '.b')
        plt.show()
        return fig, ax    


    def predict(self, X=None):
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
        if X is None:
            # 1. Predict quantitative phenotype
            ypred = self.Model.predict()
            # 2. Determine class (Dead/alive) for each phenotype
            yclasses = self.Classifier.predict()
            # Update ypred with dead phenotype information
            ypred[yclasses==0] = 0
        else:
            # 1. Predict quantitative phenotype
            ypred = self.Model.predict(X=X)
            # 2. Determine class (Dead/alive) for each phenotype
            nterms = self.Classifier.Xfit.shape[-1]
            Xclass = X[:,:nterms]
            yclasses = self.Classifier.predict(X=Xclass)
            # Update ypred with dead phenotype information
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
        #y = np.multiply(y, classes)
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
