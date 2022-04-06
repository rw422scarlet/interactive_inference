import torch
import torch.nn as nn
import pyro.distributions as pyro_dist
from torch.distributions import MultivariateNormal
from src.distributions.distributions import MultivariateSkewNormal
from src.distributions.utils import make_covariance_matrix
from src.distributions.flows import SimpleTransformedModule, BatchNormTransform

class HiddenMarkovModel(nn.Module):
    def __init__(self, state_dim, act_dim):
        super().__init__()
        self.state_dim = state_dim
        self.act_dim = act_dim
        self.parameter_size = [act_dim * state_dim * state_dim, state_dim]
        
        self.B = nn.Parameter(
            torch.randn(1, act_dim, state_dim, state_dim), requires_grad=True
        )
        self.D = nn.Parameter(torch.randn(1, state_dim), requires_grad=True)
        nn.init.xavier_normal_(self.B, gain=1.)
        nn.init.xavier_normal_(self.D, gain=1.)
        
    def __repr__(self):
        s = "{}(s={}, a={})".format(
            self.__class__.__name__, self.state_dim, self.act_dim
        )
        return s
    
    def transform_parameters(self, B):
        return B.view(-1, self.act_dim, self.state_dim, self.state_dim)
    
    def forward(self, logp_o, a, b, B=None):
        """ 
        Args:
            logp_o (torch.tensor): observation log likelihood [batch_size, state_dim]
            a (torch.tensor): soft action vector [batch_size, act_dim]
            b (torch.tensor): belief [batch_size, state_dim]
            B (torch.tensor, optional): transition parameters 
                [batch_size, act_dim, state_dim, state_dim]

        Returns:
            b_t(torch.tensor): next belief [batch_size, state_dim]
        """
        if B is not None:
            B = self.transform_parameters(B)
            B = torch.softmax(B, dim=-1)
        else:
            B = torch.softmax(self.B, dim=-1)
        
        B_a = torch.sum(B * a.unsqueeze(-1).unsqueeze(-1), dim=-3)
        logp_s = torch.log(torch.sum(b.unsqueeze(-1) * (B_a), dim=-2) + 1e-6)
        b_t = torch.softmax(logp_o + logp_s, dim=-1)
        return b_t
    
    
class ConditionalDistribution(nn.Module):
    def __init__(self, x_dim, z_dim, dist="mvn", cov="full", batch_norm=False):
        """
        Args:
            x_dim (int): observed output dimension
            z_dim (int): latent conditonal dimension
            dist (str): distribution type ["mvn", "mvsn"]
            cov (str): covariance type ["diag", "full"]
            batch_norm (bool, optional): use input batch normalization. default=True
        """
        super().__init__()
        assert dist in ["mvn", "mvsn"]
        assert cov in ["diag", "full"]
        self.x_dim = x_dim
        self.z_dim = z_dim
        self.dist = dist
        self.cov = cov
        self.parameter_size = [
            z_dim * x_dim,
            z_dim * x_dim,
            z_dim * x_dim * x_dim,
            z_dim * x_dim
        ]
        self.batch_norm = batch_norm
        
        self.mu = nn.Parameter(torch.randn(1, z_dim, x_dim), requires_grad=True)
        self.lv = nn.Parameter(torch.randn(1, z_dim, x_dim), requires_grad=True)
        self.tl = nn.Parameter(torch.randn(1, z_dim, x_dim, x_dim), requires_grad=True)
        self.sk = nn.Parameter(torch.randn(1, z_dim, x_dim), requires_grad=True)
        
        nn.init.normal_(self.mu, mean=0, std=1)
        nn.init.normal_(self.lv, mean=0, std=0.01)
        nn.init.normal_(self.tl, mean=0, std=0.01)
        nn.init.normal_(self.sk, mean=0, std=0.01)
        
        if dist == "mvn":
            nn.init.constant_(self.sk, 0)
            self.sk.requires_grad = False
            self.parameter_size = self.parameter_size[:-1]
            self.sk.data = torch.zeros_like(self.sk.data)
        
        if cov == "diag":
            nn.init.constant_(self.tl, 0)
            self.tl.requires_grad = False
            self.parameter_size = self.parameter_size[:-1]
            self.tl.data = torch.zeros_like(self.tl.data)
        
        if batch_norm:
            self.bn = BatchNormTransform(x_dim, momentum=0.1)
            self.bn.gamma.requires_grad = False
            self.bn.beta.requires_grad = False
        
    def __repr__(self):
        s = "{}(x_dim={}, z_dim={}, class={}, cov={})".format(
            self.__class__.__name__, self.x_dim, self.z_dim, self.dist, self.cov
        )
        return s
    
    def transform_parameters(self, params):
        if self.dist == "mvn":
            if self.cov == "full":
                mu, lv, tl = torch.split(params, self.parameter_size, dim=-1)
                mu = mu.view(-1, self.z_dim, self.x_dim)
                lv = lv.view(-1, self.z_dim, self.x_dim)
                tl = tl.view(-1, self.z_dim, self.x_dim, self.x_dim)
                sk = torch.zeros(len(params), self.z_dim, self.x_dim)
            elif self.cov == "diag":
                mu, lv = torch.split(params, self.parameter_size, dim=-1)
                mu = mu.view(-1, self.z_dim, self.x_dim)
                lv = lv.view(-1, self.z_dim, self.x_dim)
                tl = torch.zeros(len(params), self.z_dim, self.x_dim, self.x_dim)
                sk = torch.zeros(len(params), self.z_dim, self.x_dim)
            else:
                raise NotImplementedError
        elif self.dist == "mvsn":
            if self.cov == "full":
                mu, lv, tl, sk = torch.split(params, self.parameter_size, dim=-1)
                mu = mu.view(-1, self.z_dim, self.x_dim)
                lv = lv.view(-1, self.z_dim, self.x_dim)
                tl = tl.view(-1, self.z_dim, self.x_dim, self.x_dim)
                sk = sk.view(-1, self.z_dim, self.x_dim)
            elif self.cov == "diag":
                mu, lv, sk = torch.split(params, self.parameter_size, dim=-1)
                mu = mu.view(-1, self.z_dim, self.x_dim)
                lv = lv.view(-1, self.z_dim, self.x_dim)
                tl = torch.zeros(len(params), self.z_dim, self.x_dim, self.x_dim)
            else:
                raise NotImplementedError
        else:
            raise NotImplementedError
        
        return mu, lv, tl, sk
    
    def get_distribution_class(self, params=None):
        if params is not None:
            [mu, lv, tl, sk] = self.transform_parameters(params)
        else:
            [mu, lv, tl, sk] = self.mu, self.lv, self.tl, self.sk
        L = make_covariance_matrix(lv, tl, cholesky=True)
        
        if self.dist == "mvn":
            distribution = MultivariateNormal(mu, scale_tril=L)
        elif self.dist == "mvsn":
            distribution = MultivariateSkewNormal(mu, sk, scale_tril=L)
        
        if self.batch_norm:
            distribution = SimpleTransformedModule(distribution, [self.bn])
        return distribution
    
    def mean(self, params=None):
        distribution = self.get_distribution_class(params)
        return distribution.mean
    
    def variance(self, params=None):
        distribution = self.get_distribution_class(params)
        return distribution.variance
    
    def entropy(self, params=None):
        distribution = self.get_distribution_class(params)
        return distribution.entropy()
    
    def log_prob(self, x, params=None):
        """
        Args:
            x (torch.tensor): [batch_size, x_dim]
            params (torch.tensor, optional): parameter vector. Defaults to None.
        """
        distribution = self.get_distribution_class(params)
        return distribution.log_prob(x.unsqueeze(-2))
    
    def sample(self, sample_shape, params=None):
        distribution = self.get_distribution_class(params)
        return distribution.sample(sample_shape)
    
    def bayesian_average(self, pi, params=None):
        mu = self.mean(params)
        x = torch.sum(pi.unsqueeze(-1) * mu.unsqueeze(0), dim=-2)
        return x

    def ancestral_sample(self, pi, num_samples, params=None):
        a = torch.distributions.Categorical(pi).sample((num_samples,))
        x_ = self.sample((num_samples,), params)
        
        # sample component
        a_ = nn.functional.one_hot(a, self.z_dim).unsqueeze(-1)
        x = torch.sum(a_ * x_.unsqueeze(1), dim=-2)
        return x


""" TODO: 
subclass ConditionalDistribution 
add test case
"""
class GeneralizedLinearModel(nn.Module):
    def __init__(self, x_dim, z_dim, dist="mvn", cov="full", batch_norm=False):
        """
        Args:
            x_dim (int): observed output dimension
            z_dim (int): latent conditonal dimension
            dist (str): distribution type ["mvn", "mvsn"]
            cov (str): covariance type ["diag", "full"]
            batch_norm (bool, optional): use input batch normalization. default=True
        """
        super().__init__()
        self.x_dim = x_dim
        self.z_dim = z_dim
        self.dist = dist
        self.cov = cov
        self.parameter_size = [
            z_dim * x_dim,
            z_dim * x_dim,
            z_dim * x_dim * x_dim,
            z_dim * x_dim
        ]
        self.batch_norm = batch_norm
        
        self.mu = nn.Parameter(torch.randn(1, z_dim, x_dim), requires_grad=True)
        self.lv = nn.Parameter(torch.randn(1, z_dim, x_dim), requires_grad=True)
        self.tl = nn.Parameter(torch.randn(1, z_dim, x_dim, x_dim), requires_grad=True)
        self.sk = nn.Parameter(torch.randn(1, z_dim, x_dim), requires_grad=True)
        
        nn.init.normal_(self.mu, mean=0, std=1)
        nn.init.normal_(self.lv, mean=0, std=0.01)
        nn.init.normal_(self.tl, mean=0, std=1)
        nn.init.normal_(self.sk, mean=0, std=1)
        
        if dist == "mvn":
            nn.init.constant_(self.sk, 0)
            self.sk.requires_grad = False
            self.parameter_size = self.parameter_size[:-1]
            self.sk.data = torch.zeros_like(self.sk.data)
        
        if cov == "diag":
            nn.init.constant_(self.tl, 0)
            self.tl.requires_grad = False
            self.parameter_size = self.parameter_size[:-1]
            self.tl.data = torch.zeros_like(self.tl.data)
        
        if batch_norm:
            self.bn = BatchNormTransform(x_dim, momentum=0.1)
            self.bn.gamma.requires_grad = False
            self.bn.beta.requires_grad = False
        
    def __repr__(self):
        s = "{}(x_dim={}, z_dim={}, class={}, cov={})".format(
            self.__class__.__name__, self.x_dim, self.z_dim, self.dist, self.cov
        )
        return s

    def transform_parameters(self, params):
        if self.dist == "mvn":
            if self.cov == "full":
                mu, lv, tl = torch.split(params, self.parameter_size, dim=-1)
                mu = mu.view(-1, self.z_dim, self.x_dim)
                lv = lv.view(-1, self.z_dim, self.x_dim)
                tl = tl.view(-1, self.z_dim, self.x_dim, self.x_dim)
                sk = torch.zeros(len(params), self.z_dim, self.x_dim)
            elif self.cov == "diag":
                mu, lv = torch.split(params, self.parameter_size, dim=-1)
                mu = mu.view(-1, self.z_dim, self.x_dim)
                lv = lv.view(-1, self.z_dim, self.x_dim)
                tl = torch.zeros(len(params), self.z_dim, self.x_dim, self.x_dim)
                sk = torch.zeros(len(params), self.z_dim, self.x_dim)
            else:
                raise NotImplementedError
        elif self.dist == "mvsn":
            if self.cov == "full":
                mu, lv, tl, sk = torch.split(params, self.parameter_size, dim=-1)
                mu = mu.view(-1, self.z_dim, self.x_dim)
                lv = lv.view(-1, self.z_dim, self.x_dim)
                tl = tl.view(-1, self.z_dim, self.x_dim, self.x_dim)
                sk = sk.view(-1, self.z_dim, self.x_dim)
            elif self.cov == "diag":
                mu, lv, sk = torch.split(params, self.parameter_size, dim=-1)
                mu = mu.view(-1, self.z_dim, self.x_dim)
                lv = lv.view(-1, self.z_dim, self.x_dim)
                tl = torch.zeros(len(params), self.z_dim, self.x_dim, self.x_dim)
            else:
                raise NotImplementedError
        else:
            raise NotImplementedError
        
        return mu, lv, tl, sk
    
    def get_distribution_class(self, pi, params=None):
        if params is not None:
            [mu, lv, tl, sk] = self.transform_parameters(params)
        else:
            [mu, lv, tl, sk] = self.mu, self.lv, self.tl, self.sk
        
        # mix action units
        pi_ = pi.unsqueeze(-1)
        mu_ = torch.sum(pi_ * mu, dim=-2)
        lv_ = torch.sum(pi_ * lv * mu.abs(), dim=-2)
        tl_ = torch.sum(pi_.unsqueeze(-1) * tl * mu.abs().unsqueeze(-1), dim=-3)
        sk_ = torch.sum(pi_ * sk, dim=-2)
        L = make_covariance_matrix(lv_, tl_, cholesky=True)
        
        if self.dist == "mvn":
            distribution = MultivariateNormal(mu_, scale_tril=L)
        elif self.dist == "mvsn":
            distribution = MultivariateSkewNormal(mu_, sk_, scale_tril=L)
        
        if self.batch_norm:
            distribution = SimpleTransformedModule(distribution, [self.bn])
        return distribution
    
    def mean(self, pi, params=None):
        distribution = self.get_distribution_class(pi, params)
        return distribution.mean
    
    def variance(self, pi, params=None):
        distribution = self.get_distribution_class(pi, params)
        return distribution.variance
    
    def entropy(self, pi, params=None):
        distribution = self.get_distribution_class(pi, params)
        return distribution.entropy()
    
    def log_prob(self, x, pi, params=None):
        """
        Args:
            x (torch.tensor): observation [batch_size, x_dim]
            pi (torch.tensor): mixing weights [batch_size, z_dim]
            params (torch.tensor, optional): parameter vector. Defaults to None.
        """
        distribution = self.get_distribution_class(pi, params)
        return distribution.log_prob(x.unsqueeze(-2))
    
    def sample(self, sample_shape, pi, params=None):
        distribution = self.get_distribution_class(pi, params)
        return distribution.sample(sample_shape)
