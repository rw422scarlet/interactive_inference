import math
import torch
import torch.nn as nn
from src.distributions.nn_models import Model, MLP
from src.distributions.legacy.models import ConditionalDistribution, HiddenMarkovModel
from src.distributions.flows import BatchNormTransform

class AbstractAgent(Model):
    def __init__(self, state_dim, act_dim, obs_dim, ctl_dim, horizon):
        super().__init__()
        self.state_dim = state_dim
        self.act_dim = act_dim
        self.obs_dim = obs_dim
        self.ctl_dim = ctl_dim
        self.horizon = horizon
    
    def reset(self):
        """ Reset internal states for online inference """
        raise NotImplementedError
    
    def init_hidden(self):
        """ Initialize hidden states """
        raise NotImplementedError 

    def forward(self, o, u):
        """ Forward algorithm
        Args:
            o (torch.tensor): 
            u (torch.tensor): 
        """
        raise NotImplementedError
    
    def choose_action_batch(self, o, u, sample_method="", num_samples=1):
        """ Choose action offline for a batch of sequences 
        
        Args:
            o (torch.tensor): observation sequence. size[T, batch_size, obs_dim]
            u (torch.tensor): control sequence. size[T, batch_size, ctl_dim]
            sample_method (str, optional): sampling method. Default=""
            num_samples (int, optional): number of samples to draw. Default=1
        """
        raise NotImplementedError

    def choose_action(self, o, u, sample_method="", num_samples=1):
        """ Choose action online for a single time step
        
        Args:
            o (torch.tensor): observation sequence. size[batch_size, obs_dim]
            u (torch.tensor): control sequence. size[batch_size, ctl_dim]
            sample_method (str, optional): sampling method. Default=""
            num_samples (int, optional): number of samples to draw. Default=1
        """
        raise NotImplementedError


class StructuredRecurrentAgent(AbstractAgent):
    """ Recurrent agent with same components as the active inference agent """
    def __init__(
        self, state_dim, act_dim, obs_dim, ctl_dim, H, 
        ctl_dist="mvn", ctl_cov="full", hidden_dim=32, num_hidden=2
        ):
        super().__init__(state_dim, act_dim, obs_dim, ctl_dim, H)
        self.D = nn.Parameter(torch.randn(1, state_dim), requires_grad=True)
        self.rnn = nn.GRU(obs_dim, state_dim)
        self.planner = MLP(state_dim, act_dim, hidden_dim, num_hidden, "relu")
        self.ctl_model = ConditionalDistribution(ctl_dim, act_dim, ctl_dist, ctl_cov, batch_norm=True)
        self.bn = BatchNormTransform(obs_dim, affine=False)

        nn.init.xavier_normal_(self.D, gain=1.)

    def reset(self):
        self._b = None
        self._a = None
    
    def forward(self, o, u, h=None, theta=None, inference=False):
        if h is None:
            h0, _ = self.init_hidden(o)
        else:
            h0 = h

        o = self.bn._inverse(o)
        h, _ = self.rnn(o, h0)
        h = torch.cat([h0, h], dim=0)
        b = torch.softmax(h, dim=-1)
        
        a = self.planner(b)
        a = torch.softmax(a, dim=-1)
        
        if not inference:
            logp_pi = self.ctl_model.mixture_log_prob(a[:-1], u)
            logp_obs = torch.zeros_like(o)[:, :, 0]
            return logp_pi, logp_obs
        else:
            return h, a

    def init_hidden(self, o):
        h0 = self.D.unsqueeze(0) * torch.ones(o.shape[-2], self.state_dim)
        a0 = self.planner(torch.softmax(h0, dim=-1))
        a0 = torch.softmax(a0, dim=-1)
        return h0, a0

    def choose_action(self, o, u, batch=False, theta=None, num_samples=None):
        if batch:
            b, a = self.forward(o, u, inference=True)
            b, a = b[:-1], a[:-1]
        else:
            o, u = o.unsqueeze(0), u.unsqueeze(0)
            if self._b is None: # initial step
                b, a = self.init_hidden(o)
            else:
                h = self._b
                b, a = self.forward(o, u, h=h, inference=True)
                b, a = b[1:], a[1:]
            self._b, self._a = b, a

        if num_samples is None:
            u_pred = self.ctl_model.bayesian_average(a)
        else:
            u_pred = self.ctl_model.ancestral_sample(a, num_samples)
        return u_pred.squeeze(-3)


class FullyRecurrentAgent(AbstractAgent):
    """ Recurrent agent with fully neural network components 
        ctl_model and bn modules are dummy for eval scripts
    """
    def __init__(
        self, state_dim, act_dim, obs_dim, ctl_dim, H, 
        ctl_dist="mvn", ctl_cov="full", hidden_dim=32, num_hidden=2
        ):
        super().__init__(state_dim, act_dim, obs_dim, ctl_dim, H)
        self.D = nn.Parameter(torch.randn(1, state_dim), requires_grad=True)
        self.rnn = nn.GRU(obs_dim, state_dim)
        self.planner = MLP(state_dim, ctl_dim * 2, hidden_dim, num_hidden, "relu")
        self.ctl_model = ConditionalDistribution(ctl_dim, act_dim, ctl_dist, ctl_cov, batch_norm=True)
        self.bn = BatchNormTransform(obs_dim, affine=False)

        nn.init.xavier_normal_(self.D, gain=1.)

    def reset(self):
        self._b = None
        self._a = None
    
    def forward(self, o, u, h=None, theta=None, inference=False):
        if h is None:
            h0, _ = self.init_hidden(o)
        else:
            h0 = h

        o = self.bn._inverse(o)
        h, _ = self.rnn(o, h0)
        h = torch.cat([h0, h], dim=0)
        b = torch.softmax(h, dim=-1)
        
        a = self.planner(b)
        
        if not inference:
            mu, lv = torch.split(a[1:], self.ctl_dim, dim=-1)
            sd = lv.clip(math.log(1e-6), math.log(1e6)).exp()
            logp_pi = torch.distributions.Normal(mu, sd).log_prob(u).sum(-1)
            logp_obs = torch.zeros_like(o)[:, :, 0]
            return logp_pi, logp_obs
        else:
            return h, a

    def init_hidden(self, o):
        h0 = self.D.unsqueeze(0) * torch.ones(o.shape[-2], self.state_dim)
        a0 = self.planner(torch.softmax(h0, dim=-1))
        return h0, a0

    def choose_action(self, o, u, batch=False, theta=None, num_samples=None):
        if batch:
            b, a = self.forward(o, u, inference=True)
            b, a = b[:-1], a[1:]
        else:
            o, u = o.unsqueeze(0), u.unsqueeze(0)
            if self._b is None: # initial step
                b, a = self.init_hidden(o)
            # else:
            #     print("step 1")
            #     h = self._b
            #     b, a = self.forward(o, u, h=h, inference=True)
            #     b, a = b[1:], a[1:]
            # print("step 1")
            h = self._b
            b, a = self.forward(o, u, h=h, inference=True)
            b, a = b[1:], a[1:]
            self._b, self._a = b, a
        
        mu, lv = torch.split(a, self.ctl_dim, dim=-1)
        if num_samples is None:
            u_pred = mu
        else:
            sd = lv.clip(math.log(1e-6), math.log(1e6)).exp()
            u_pred = torch.distributions.Normal(mu, sd).sample((num_samples,))
        return u_pred.squeeze(-3)


class HMMRecurrentAgent(AbstractAgent):
    """ Recurrent agent with HMM recurrent units and fully connected output units
        ctl_model and bn modules are dummy for eval scripts
    """
    def __init__(
        self, state_dim, act_dim, obs_dim, ctl_dim, H, 
        ctl_dist="mvn", ctl_cov="full", hidden_dim=32, num_hidden=2
        ):
        super().__init__(state_dim, act_dim, obs_dim, ctl_dim, H)
        self.D = nn.Parameter(torch.randn(1, state_dim), requires_grad=True)
        self.hmm = HiddenMarkovModel(state_dim, act_dim)
        self.obs_model = ConditionalDistribution(obs_dim, state_dim, "mvn", "full", batch_norm=True)
        self.ctl_model = ConditionalDistribution(ctl_dim, act_dim, ctl_dist, ctl_cov, batch_norm=True)
        self.planner = MLP(state_dim, ctl_dim * 2, hidden_dim, num_hidden, "relu")
        self.bn = BatchNormTransform(obs_dim, affine=False)

        nn.init.xavier_normal_(self.D, gain=1.)

    def reset(self):
        self._b = None
        self._a = None
    
    def forward(self, o, u, h=None, theta=None, inference=False):
        T = len(u)
        b = [torch.empty(0)] * (T + 1)
        a = [torch.empty(0)] * (T + 1)
        if h is None:
            b[0], a[0], _ = self.init_hidden(o)
        else:
            b[0], a[0] = h
        
        logp_o = self.obs_model.log_prob(o)
        logp_u = self.ctl_model.log_prob(u)
        for t in range(T):
            a[t+1] = torch.softmax(logp_u[t], dim=-1)
            b[t+1] = self.hmm(logp_o[t], a[t+1], b[t])
        a = torch.stack(a)
        b = torch.stack(b)
        
        pi = self.planner(b)
        if not inference:
            mu, lv = torch.split(pi[:-1], self.ctl_dim, dim=-1)
            sd = lv.clip(math.log(1e-6), math.log(1e6)).exp()
            logp_pi = torch.distributions.Normal(mu, sd).log_prob(u).sum(-1)
            logp_b = torch.log(b[1:] + 1e-6)
            logp_obs = torch.logsumexp(logp_b + logp_o, dim=-1)
            return logp_pi, logp_obs
        else:
            return b, a, pi

    def init_hidden(self, o):
        b0 = torch.softmax(self.D, dim=-1) 
        b0 = b0 * torch.ones(o.shape[-2], self.state_dim)
        a0 = torch.softmax(torch.ones(o.shape[-2], self.act_dim), dim=-1)
        pi0 = self.planner(b0).unsqueeze(0)
        return b0, a0, pi0

    def choose_action(self, o, u, batch=False, theta=None, num_samples=None):
        if batch:
            b, a, pi = self.forward(o, u, inference=True)
            b, a, pi = b[:-1], a[:-1], pi[:-1]
        else:
            o, u = o.unsqueeze(0), u.unsqueeze(0)
            if self._b is None: # initial step
                b, a, pi = self.init_hidden(o)
            else:
                h = [self._b, self._a]
                b, a, pi = self.forward(o, u, h=h, inference=True)
                b, a, pi = b[1:], a[1:], pi[1:]
            self._b, self._a = b.view(-1, self.state_dim), a.view(-1, self.act_dim)
        
        mu, lv = torch.split(pi, self.ctl_dim, dim=-1)
        if num_samples is None:
            u_pred = mu
        else:
            sd = lv.clip(math.log(1e-6), math.log(1e6)).exp()
            u_pred = torch.distributions.Normal(mu, sd).sample((num_samples,))
        return u_pred.squeeze(-3)


""" TODO: unify all baseline agents """
class StructuredHMMRecurrentAgent(AbstractAgent):
    """ Recurrent agent with HMM recurrent units, fully connected planner, 
        and gaussian mixture control model
    """
    def __init__(
        self, state_dim, act_dim, obs_dim, ctl_dim, H, 
        ctl_dist="mvn", ctl_cov="full", hidden_dim=32, num_hidden=2
        ):
        super().__init__(state_dim, act_dim, obs_dim, ctl_dim, H)
        self.D = nn.Parameter(torch.randn(1, state_dim), requires_grad=True)
        self.hmm = HiddenMarkovModel(state_dim, act_dim)
        self.obs_model = ConditionalDistribution(obs_dim, state_dim, "mvn", "full", batch_norm=True)
        self.ctl_model = ConditionalDistribution(ctl_dim, act_dim, ctl_dist, ctl_cov, batch_norm=True)
        self.planner = MLP(state_dim, act_dim, hidden_dim, num_hidden, "relu")
        self.bn = BatchNormTransform(obs_dim, affine=False)

        nn.init.xavier_normal_(self.D, gain=1.)

    def reset(self):
        self._b = None
        self._a = None
    
    def forward(self, o, u, h=None, theta=None, inference=False):
        T = len(u)
        b = [torch.empty(0)] * (T + 1)
        a = [torch.empty(0)] * (T + 1)
        if h is None:
            b[0], a[0] = self.init_hidden(o)
        else:
            b[0], a[0] = h
        
        logp_o = self.obs_model.log_prob(o)
        logp_u = self.ctl_model.log_prob(u)
        for t in range(T):
            p_a = self.infer_action(a[t], logp_u[t])
            b[t+1] = self.hmm(logp_o[t], p_a, b[t])
            a[t+1] = torch.softmax(self.planner(b[t+1]), dim=-1)
        a = torch.stack(a)
        b = torch.stack(b)
        
        if not inference:
            logp_pi = self.ctl_model.mixture_log_prob(a[:-1], u)
            logp_b = torch.log(b[1:] + 1e-6)
            logp_obs = torch.logsumexp(logp_b + logp_o, dim=-1)
            return logp_pi, logp_obs
        else:
            return b, a

    def init_hidden(self, o):
        b0 = torch.softmax(self.D, dim=-1) 
        b0 = b0 * torch.ones(o.shape[-2], self.state_dim)        
        a0 = torch.softmax(self.planner(b0), dim=-1)
        return b0, a0
    
    def infer_action(self, a, logp_u):
        p_a = torch.softmax(torch.log(a + 1e-6) + logp_u, dim=-1)
        return p_a

    def choose_action(self, o, u, batch=False, theta=None, num_samples=None):
        if batch:
            b, a = self.forward(o, u, inference=True)
            b, a = b[:-1], a[:-1]
        else:
            o, u = o.unsqueeze(0), u.unsqueeze(0)
            if self._b is None: # initial step
                b, a = self.init_hidden(o)
            else:
                h = [self._b, self._a]
                b, a = self.forward(o, u, h=h, inference=True)
                b, a = b[1:], a[1:]
            self._b, self._a = b.view(-1, self.state_dim), a.view(-1, self.act_dim)
        
        if num_samples is None:
            u_pred = self.ctl_model.bayesian_average(a)
        else:
            u_pred = self.ctl_model.ancestral_sample(a, num_samples)
        return u_pred.squeeze(-3)


class ExpertNetwork(nn.Module):
    def __init__(self, act_dim, obs_dim, ctl_dim, nb=False, prod=False):
        """ Naive Bayes classifier + gaussian mixture output
        Args:
            act_dim (int): action dimension
            obs_dim (int): observation dimension
            ctl_dim (int): control dimension
            nb (bool, optional): naive bayes observation model. Defaults to False.
            prod (bool, optional): product of experts observation model. Defaults to False.
        """
        super().__init__()
        self.prod = prod
        self.nb = nb
        self.act_dim = act_dim
        self.obs_dim = obs_dim
        self.ctl_dim = ctl_dim
        
        self.lin = nn.Linear(obs_dim, act_dim)
        self.mu = nn.Parameter(torch.randn(1, act_dim, ctl_dim), requires_grad=True)
        self.lv = nn.Parameter(torch.randn(1, act_dim, ctl_dim), requires_grad=True)
        
        nn.init.xavier_normal_(self.mu, gain=1.)
        nn.init.xavier_normal_(self.lv, gain=1.)
        
        if self.nb:
            self.b0 = nn.Parameter(torch.randn(1, act_dim), requires_grad=True)
            self.mu_o = nn.Parameter(torch.randn(1, act_dim, obs_dim), requires_grad=True)
            self.lv_o = nn.Parameter(torch.randn(1, act_dim, obs_dim), requires_grad=True)
            
            nn.init.xavier_normal_(self.b0, gain=0.3)
            nn.init.xavier_normal_(self.mu_o, gain=1.)
            nn.init.xavier_normal_(self.lv_o, gain=1.)
    
    def __repr__(self):
        s = "{}(act_dim={}, naive_bayes={}, prod_experts={})".format(
            self.__class__.__name__, self.act_dim, self.nb, self.prod
        )
        return s
    
    def forward(self, o, u, inference=False):
        """
        Args:
            o (torch.tensor): observation sequence [T, batch_size, obs_dim]
            u (torch.tensor): control sequence [T, batch_size, ctl_dim]
            inference (bool, optional): whether in inference model. Defaults to False
            
        Returns:
            logp_pi (torch.tensor): predicted control likelihood [T, batch_size]
            logp_obs (torch.tensor): predicted observation likelihood [T, batch_size]
        """
        # recognition
        if self.nb:
            log_b0 = torch.softmax(self.b0, dim=-1).log()
            logp_o = torch.distributions.Normal(
                self.mu_o, self.lv_o.exp()
            ).log_prob(o.unsqueeze(-2)).sum(dim=-1)
            p_a = torch.softmax(log_b0 + logp_o, dim=-1)
            
            logp_obs = torch.logsumexp(torch.log(p_a + 1e-6) + logp_o, dim=-1)
        else:
            p_a = torch.softmax(self.lin(o), dim=-1)
            logp_obs = torch.zeros_like(o[:, :, 0])
        
        # control
        if self.prod:
            mu = p_a.matmul(self.mu.unsqueeze(0))
            lv = p_a.matmul(self.lv.unsqueeze(0))
            logp_pi = torch.distributions.Normal(mu, lv.exp()).log_prob(u).sum(dim=-1)
        else:
            logp_a = torch.distributions.Normal(
                self.mu, self.lv.exp()
            ).log_prob(u.unsqueeze(-2)).sum(dim=-1)
            logp_pi = torch.logsumexp(torch.log(p_a + 1e-6) + logp_a, dim=-1)
        
        if not inference:
            return logp_pi, logp_obs
        else:
            return p_a
    
    def choose_action(self, o, u):
        """ Choose action via Bayesian model averaging

        Args:
            o (torch.tensor): observation sequence [T, batch_size, obs_dim]
            u (torch.tensor): control sequence [T, batch_size, ctl_dim]

        Returns:
            u: predicted control [T, batch_size, ctl_dim]
        """
        p_a = self.forward(o, u, inference=True)
        
        # bayesian model averaging
        u = p_a.matmul(self.mu)
        return u